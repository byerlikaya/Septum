"""Resolve the client address of a request.

Proxy headers are not parsed here. uvicorn's ``ProxyHeadersMiddleware``
rewrites ``request.client`` from ``X-Forwarded-For`` only when the direct
peer is listed in ``FORWARDED_ALLOW_IPS``; everything else sees the real
peer. This helper reads that result and folds IPv4-mapped IPv6 addresses
(``::ffff:203.0.113.5``) into plain IPv4 so one client gets one identity.
"""

from __future__ import annotations

import ipaddress
from typing import Optional

from starlette.requests import Request


def resolve_client_ip(request: Request) -> Optional[str]:
    """Return the normalised client address, or ``None`` when the peer is unknown."""
    client = request.client
    if client is None or not client.host:
        return None
    host = client.host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    return str(address)
