"""Connection-bound SSRF protection for Brightspace bearer clients.

The URL hostname is retained for TLS SNI, certificate verification, and the
HTTP Host/:authority value. The network backend resolves and classifies DNS at
connection time, then pins the socket to the validated public address.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Iterable, Optional

import anyio
import httpx
from httpcore._backends.auto import AutoBackend
from httpcore._backends.base import (
    SOCKET_OPTION,
    AsyncNetworkBackend,
    AsyncNetworkStream,
)

from src.utils.security import _is_forbidden_address


class BrightspaceSafeNetworkBackend(AsyncNetworkBackend):
    """Resolve, classify, and pin each Brightspace TCP connection."""

    def __init__(
        self, *, network_backend: Optional[AsyncNetworkBackend] = None
    ) -> None:
        self._network_backend = network_backend or AutoBackend()

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
            raise ValueError("Could not resolve Brightspace outbound hostname") from exc
        if not addr_infos:
            raise ValueError("Could not resolve Brightspace outbound hostname")

        addresses: list[str] = []
        for addr_info in addr_infos:
            address_text = str(addr_info[4][0]).split("%", 1)[0]
            address = ipaddress.ip_address(address_text)
            if _is_forbidden_address(address):
                raise ValueError("Brightspace outbound URL target is not allowed")
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
        raise ValueError("Brightspace HTTP transport does not allow Unix sockets")

    async def sleep(self, seconds: float) -> None:
        await self._network_backend.sleep(seconds)


class BrightspaceSafeAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """httpx transport with connection-bound Brightspace DNS validation."""

    def __init__(
        self, *, network_backend: Optional[AsyncNetworkBackend] = None
    ) -> None:
        super().__init__(verify=True, trust_env=False)
        self._pool._network_backend = BrightspaceSafeNetworkBackend(
            network_backend=network_backend
        )


def create_brightspace_safe_transport() -> BrightspaceSafeAsyncHTTPTransport:
    """Create a hardened transport for Brightspace bearer requests."""
    return BrightspaceSafeAsyncHTTPTransport()


__all__ = [
    "BrightspaceSafeAsyncHTTPTransport",
    "BrightspaceSafeNetworkBackend",
    "create_brightspace_safe_transport",
]
