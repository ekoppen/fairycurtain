"""
Guarded fetching of image URLs that come from outside (MQTT messages, pushed
now-playing info, JSON from a music API).

Guards: http(s) only, no loopback/link-local/multicast/reserved targets, no
redirects, 10 s timeout, size cap. Private LAN addresses stay allowed, so a Home
Assistant on the LAN keeps working. The address check runs inside the resolver
that aiohttp itself connects with, so DNS cannot change between check and use.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlsplit

from . import render as R

FETCH_TIMEOUT = 10


def check_ip(addr: str) -> None:
    """Refuse addresses a URL fetch must never reach."""
    ip = ipaddress.ip_address(addr)
    ip = getattr(ip, "ipv4_mapped", None) or ip
    if (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved
            or (ip.version == 4 and ip in ipaddress.ip_network("0.0.0.0/8"))):
        raise ValueError(f"refusing to fetch from {ip}")


async def fetch_image(url: str, headers: Optional[dict] = None, trusted: bool = False) -> bytes:
    """Fetch an image. `trusted=True` (a URL from the admin's own config, e.g. the
    Home Assistant base URL) skips the address check but keeps scheme/size/timeout."""
    import aiohttp
    from aiohttp.abc import AbstractResolver
    from aiohttp.resolver import ThreadedResolver

    class SafeResolver(AbstractResolver):
        def __init__(self) -> None:
            self._inner = ThreadedResolver()

        async def resolve(self, host, port=0, family=socket.AF_INET):
            res = await self._inner.resolve(host, port, family)
            for r in res:
                check_ip(r["host"])
            return res

        async def close(self) -> None:
            await self._inner.close()

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("only http(s) image URLs are allowed")
    connector = None
    if not trusted:
        try:  # IP literals bypass the resolver, so check them here
            check_ip(parts.hostname)
        except ValueError as e:
            if "refusing" in str(e):
                raise
        connector = aiohttp.TCPConnector(resolver=SafeResolver())
    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as s, \
            s.get(url, headers=headers or {}, allow_redirects=False) as r:
        r.raise_for_status()
        data = await r.content.read(R.MAX_IMAGE_BYTES + 1)
    if len(data) > R.MAX_IMAGE_BYTES:
        raise ValueError("image too large")
    return data
