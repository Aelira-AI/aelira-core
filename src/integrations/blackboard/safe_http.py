"""Connection-bound SSRF protection for Blackboard bearer clients."""

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

from src.utils.security import _is_forbidden_address


class BlackboardSafeNetworkBackend(AsyncNetworkBackend):
    """Resolve, classify, and pin every Blackboard TCP connection."""

    def __init__(
        self,
        development_origin: Optional[str] = None,
        *,
        network_backend: Optional[AsyncNetworkBackend] = None,
    ) -> None:
        self._network_backend = network_backend or AutoBackend()
        self._development_host: Optional[str] = None
        self._development_port: Optional[int] = None
        if development_origin and os.getenv("ENV", "development").lower() in {
            "development",
            "test",
        }:
            parsed = urlparse(development_origin)
            if parsed.hostname == "localhost":
                self._development_host = "localhost"
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
            raise ValueError("Could not resolve Blackboard outbound hostname") from exc
        if not addr_infos:
            raise ValueError("Could not resolve Blackboard outbound hostname")

        allow_test_localhost = (
            host.lower().rstrip(".") == self._development_host
            and port == self._development_port
        )
        addresses: list[str] = []
        for addr_info in addr_infos:
            address_text = str(addr_info[4][0]).split("%", 1)[0]
            address = ipaddress.ip_address(address_text)
            if _is_forbidden_address(address) and not allow_test_localhost:
                raise ValueError("Blackboard outbound URL target is not allowed")
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
        raise ValueError("Blackboard HTTP transport does not allow Unix sockets")

    async def sleep(self, seconds: float) -> None:
        await self._network_backend.sleep(seconds)


class BlackboardSafeAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """httpx transport with connection-bound Blackboard DNS validation."""

    def __init__(
        self,
        development_origin: Optional[str] = None,
        *,
        network_backend: Optional[AsyncNetworkBackend] = None,
    ) -> None:
        super().__init__(verify=True, trust_env=False)
        self._pool._network_backend = BlackboardSafeNetworkBackend(
            development_origin, network_backend=network_backend
        )


def create_blackboard_safe_transport(
    development_origin: Optional[str] = None,
) -> BlackboardSafeAsyncHTTPTransport:
    """Create a hardened transport for Blackboard bearer requests."""
    return BlackboardSafeAsyncHTTPTransport(development_origin)


__all__ = [
    "BlackboardSafeAsyncHTTPTransport",
    "BlackboardSafeNetworkBackend",
    "create_blackboard_safe_transport",
]
