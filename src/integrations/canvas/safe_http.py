"""Connection-bound SSRF protection for Canvas HTTP clients.

The request URL keeps its original hostname, so httpcore continues to use that
hostname for TLS SNI/certificate verification and HTTP Host/:authority. Only
the TCP destination passed to the underlying network backend is replaced with
a DNS answer that was classified immediately before connecting.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Iterable, Optional
from urllib.parse import urlparse

import anyio
import httpx
from httpcore._backends.auto import AutoBackend
from httpcore._backends.base import (
    SOCKET_OPTION,
    AsyncNetworkBackend,
    AsyncNetworkStream,
)

from src.utils.security import _is_forbidden_address, is_canvas_development_origin


class CanvasSafeNetworkBackend(AsyncNetworkBackend):
    """Resolve, classify, and pin each Canvas TCP connection to a safe IP."""

    def __init__(
        self,
        development_origin: Optional[str] = None,
        *,
        network_backend: Optional[AsyncNetworkBackend] = None,
    ) -> None:
        self._network_backend = network_backend or AutoBackend()
        self._development_host: Optional[str] = None
        self._development_port: Optional[int] = None

        if development_origin and is_canvas_development_origin(development_origin):
            parsed = urlparse(development_origin)
            self._development_host = parsed.hostname
            self._development_port = parsed.port or (
                443 if parsed.scheme == "https" else 80
            )

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream:
        try:
            addr_infos = await anyio.to_thread.run_sync(
                lambda: socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            )
        except socket.gaierror as exc:
            raise ValueError("Could not resolve Canvas outbound hostname") from exc
        if not addr_infos:
            raise ValueError("Could not resolve Canvas outbound hostname")

        allow_development_private = (
            os.getenv("ENV", "development").lower() == "development"
            and host.lower().rstrip(".") == self._development_host
            and port == self._development_port
        )
        addresses = []
        for addr_info in addr_infos:
            address_text = str(addr_info[4][0]).split("%", 1)[0]
            address = ipaddress.ip_address(address_text)
            if _is_forbidden_address(address) and not allow_development_private:
                raise ValueError("Canvas outbound URL target is not allowed")
            addresses.append(address_text)

        return await self._network_backend.connect_tcp(
            addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: Optional[float] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream:
        raise ValueError("Canvas HTTP transport does not allow Unix sockets")

    async def sleep(self, seconds: float) -> None:
        await self._network_backend.sleep(seconds)


class CanvasSafeAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """httpx transport whose httpcore pool uses CanvasSafeNetworkBackend."""

    def __init__(self, development_origin: Optional[str] = None) -> None:
        # Keep TLS verification enabled. trust_env=False also prevents environment
        # CA overrides here; clients separately disable environment proxy routing.
        super().__init__(verify=True, trust_env=False)
        self._pool._network_backend = CanvasSafeNetworkBackend(development_origin)


def create_canvas_safe_transport(
    development_origin: Optional[str] = None,
) -> CanvasSafeAsyncHTTPTransport:
    """Create a Canvas-only transport with connection-bound DNS validation."""
    return CanvasSafeAsyncHTTPTransport(development_origin)


__all__ = [
    "CanvasSafeAsyncHTTPTransport",
    "CanvasSafeNetworkBackend",
    "create_canvas_safe_transport",
]
