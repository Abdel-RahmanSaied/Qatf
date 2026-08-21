"""Which hosts stage 3 may be pointed at.

`base_url` decides who receives the transcript AND the `Authorization: Bearer`
header, and the API has no authentication in front of it. Freely editable it is
a one-request credential-exfiltration path: point it at a host you control and
the next job posts your content and your key to you.

The same class of field as `pipeline.fetch.validate_url`, and validated here
rather than only in the router for the same reason `language` is checked in
`asr.cache_path`: this is the layer that attaches the credential, so this is the
layer that owns the risk. The router checks it again so a refusal is a
synchronous 403 instead of a job that dies on a worker thread.

A blunt "https on a public allowlist" rule cannot be used: the documented
self-hosting path is `http://ollama:11434/v1` — plain HTTP on an internal
container hostname — and refusing that would break a shipped feature.
"""

from __future__ import annotations

import functools
import ipaddress
import socket
from urllib.parse import urlsplit

from ..core.errors import InvalidBaseURL
from .presets import PRESETS


@functools.lru_cache(maxsize=1)
def known_hosts() -> frozenset[str]:
    """Hosts the shipped presets already talk to.

    Cached because `PRESETS` is a module-level literal that cannot change while
    the process is up."""
    return frozenset(
        host for p in PRESETS.values()
        if p.base_url and (host := urlsplit(p.base_url).hostname)
    )


def _is_internal(host: str) -> bool:
    """True when this host is somewhere only the deployment can reach.

    Resolvable: EVERY address must be private. Every, not any — a name that
    publishes one private and one public record would otherwise walk straight
    through by having its private record checked first, which is a bypass rather
    than an edge case. A literal IP resolves to itself, so `127.0.0.1` and
    `10.1.2.3` take this path and need no separate branch.

    UNRESOLVABLE and SINGLE-LABEL: accepted. This case was found by live
    verification rather than by the suite. `ollama` had been stopped for thirty
    hours, so the name did not resolve, so "every address is private" was
    vacuously false and its URL was refused — logically consistent, and exactly
    backwards in practice: you cannot configure a service's address before you
    start it, and starting it is when you would want to.

    A single label is the discriminator that makes accepting it safe. Container
    and Kubernetes service names have no dot; a host reachable on the public
    internet always does. So an unresolvable `ollama` is an internal service
    that is not up yet, while an unresolvable `whatever.example.com` could begin
    resolving anywhere and stays refused.

    A single-label name that DOES resolve still has to resolve privately — a
    search domain can turn `foo` into `foo.corp.example.com`, and that is a
    public address wearing an internal-looking name."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # No dot means no public DNS name. Anything else could become one.
        return "." not in host
    addrs = {info[4][0] for info in infos}
    if not addrs:
        return "." not in host
    return all(
        (ip := ipaddress.ip_address(a)).is_private or ip.is_loopback
        or ip.is_link_local
        for a in addrs
    )


def validate_base_url(url: str) -> str:
    """Return `url` unchanged, or raise `InvalidBaseURL`.

    Never names the rejected value in the message. The refusal reaches a caller,
    and a validator that formats input into its own message defeats the handler
    that strips it — the same trap `whisper`, `preset` and `resolution` all fell
    into."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise InvalidBaseURL("base_url must use http or https")
    if parts.username or parts.password:
        raise InvalidBaseURL("base_url must not carry userinfo")
    host = parts.hostname
    if not host:
        raise InvalidBaseURL("base_url must name a host")
    # Exact membership, never endswith: openrouter.ai.evil.net ends with the
    # right string and is a completely different host.
    if host in known_hosts() or _is_internal(host):
        return url
    raise InvalidBaseURL(
        "base_url must be a known provider host or a private address. Known: "
        + ", ".join(sorted(known_hosts()))
    )
