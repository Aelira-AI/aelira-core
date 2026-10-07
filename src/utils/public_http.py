"""HTTPX transport that connects only to validated public IP addresses.

The request retains its hostname for the HTTP Host header and TLS SNI and
certificate verification. Only the TCP destination is replaced by an IP
address validated at the connection boundary.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Iterable, Optional

import anyio
import httpcore
import httpx
import requests
from httpcore._backends.auto import AutoBackend
from httpcore._backends.base import (
    SOCKET_OPTION,
    AsyncNetworkBackend,
    AsyncNetworkStream,
)

from src.utils.security import _is_forbidden_address
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError


class PublicOnlyNetworkBackend(AsyncNetworkBackend):
    """Resolve and validate all answers, then connect to one checked address."""

    def __init__(self, *, network_backend: Optional[AsyncNetworkBackend] = None):
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
        except (socket.gaierror, ValueError) as exc:
            raise ValueError("Could not resolve outbound hostname") from exc
        if not addr_infos:
            raise ValueError("Could not resolve outbound hostname")

        addresses = []
        for addr_info in addr_infos:
            address_text = str(addr_info[4][0]).split("%", 1)[0]
            try:
                address = ipaddress.ip_address(address_text)
            except ValueError as exc:
                raise ValueError("Invalid outbound address") from exc
            if _is_forbidden_address(address):
                raise ValueError("Outbound URL target is not allowed")
            addresses.append(address_text)

        last_error = None
        for address in addresses:
            try:
                return await self._network_backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (OSError, httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is None:
            raise ValueError("No outbound address was available")
        raise last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: Optional[float] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream:
        raise ValueError("Public HTTP transport does not allow Unix sockets")

    async def sleep(self, seconds: float) -> None:
        await self._network_backend.sleep(seconds)


class PublicOnlyAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX transport with connection-bound public-address validation."""

    def __init__(self) -> None:
        # The project pins HTTPX/httpcore; this follows its Canvas transport.
        # Avoid environment proxy routing and CA overrides for untrusted URLs.
        super().__init__(verify=True, trust_env=False)
        self._pool._network_backend = PublicOnlyNetworkBackend()


class _PublicOnlyConnection:
    """Connect with urllib3 to a public DNS answer without changing the URL host."""

    def _new_conn(self) -> socket.socket:
        host = self.host
        port = self.port
        try:
            addr_infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("Could not resolve outbound hostname") from exc
        if not addr_infos:
            raise ValueError("Could not resolve outbound hostname")

        for addr_info in addr_infos:
            address_text = str(addr_info[4][0]).split("%", 1)[0]
            try:
                address = ipaddress.ip_address(address_text)
            except ValueError as exc:
                raise ValueError("Invalid outbound address") from exc
            if _is_forbidden_address(address):
                raise ValueError("Outbound URL target is not allowed")

        last_error = None
        for addr_info in addr_infos:
            try:
                return _connect_checked_sockaddr(
                    addr_info, self.timeout, self.source_address, self.socket_options
                )
            except OSError as exc:
                last_error = exc
        if last_error is None:
            raise ValueError("No outbound address was available")
        if isinstance(last_error, socket.timeout):
            raise ConnectTimeoutError(
                self,
                f"Connection to {host} timed out (connect timeout={self.timeout})",
            ) from last_error
        raise NewConnectionError(
            self, f"Failed to establish a new connection: {last_error}"
        ) from last_error


def _connect_checked_sockaddr(addr_info, timeout, source_address, socket_options):
    """Open a socket to the already validated sockaddr without another lookup."""
    family, socktype, proto, _, sockaddr = addr_info
    sock = socket.socket(family, socktype, proto)
    try:
        for option in socket_options or ():
            sock.setsockopt(*option)
        sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        sock.connect(sockaddr)
        return sock
    except BaseException:
        sock.close()
        raise


class PublicOnlyHTTPConnection(_PublicOnlyConnection, HTTPConnection):
    """HTTP connection that pins TCP to a validated public address."""


class PublicOnlyHTTPSConnection(_PublicOnlyConnection, HTTPSConnection):
    """HTTPS connection that keeps urllib3's hostname and certificate checks."""


class PublicOnlyHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = PublicOnlyHTTPConnection


class PublicOnlyHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = PublicOnlyHTTPSConnection


class PublicOnlyHTTPAdapter(requests.adapters.HTTPAdapter):
    """Requests adapter using the public-only connection classes."""

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": PublicOnlyHTTPConnectionPool,
            "https": PublicOnlyHTTPSConnectionPool,
        }
