"""Resolve client addresses across an explicitly trusted proxy boundary."""

from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

from fastapi import Request

from ..config.settings import get_settings

IPAddress = IPv4Address | IPv6Address


def _parse_address(value: str | None) -> IPAddress | None:
    if not value:
        return None
    candidate = value.strip()
    if "%" in candidate or "[" in candidate or "]" in candidate:
        return None
    try:
        return ip_address(candidate)
    except ValueError:
        return None


def _trusted_networks(cidrs: str) -> tuple:
    return tuple(
        ip_network(entry.strip(), strict=True)
        for entry in cidrs.split(",")
        if entry.strip()
    )


def _is_trusted(address: IPAddress, networks: tuple) -> bool:
    return any(
        address.version == network.version and address in network
        for network in networks
    )


def get_client_ip(request: Request, trusted_proxy_cidrs: str | None = None) -> str:
    """Return the authoritative client IP for a request.

    Forwarding headers are considered only when the transport peer is trusted.
    For a proxy chain, resolution walks from the peer toward the client and
    returns the first untrusted hop. Invalid forwarded values fail closed to
    the direct peer rather than partially trusting a malformed chain.
    """
    peer_text = request.client.host if request.client else None
    peer = _parse_address(peer_text)
    if peer is None:
        return "unknown"

    configured = (
        get_settings().trusted_proxy_cidrs
        if trusted_proxy_cidrs is None
        else trusted_proxy_cidrs
    )
    networks = _trusted_networks(configured)
    if not networks or not _is_trusted(peer, networks):
        return str(peer)

    forwarded_values = request.headers.getlist("x-forwarded-for")
    if len(forwarded_values) == 1:
        raw_chain = [part.strip() for part in forwarded_values[0].split(",")]
        if not 1 <= len(raw_chain) <= 32 or any(not part for part in raw_chain):
            return str(peer)
        chain = [_parse_address(part) for part in raw_chain]
        if any(address is None for address in chain):
            return str(peer)

        for address in reversed([*chain, peer]):
            if not _is_trusted(address, networks):
                return str(address)
        return str(chain[0])
    return str(peer)
