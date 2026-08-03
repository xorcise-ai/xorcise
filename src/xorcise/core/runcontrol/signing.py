"""HMAC signing for short-lived attachment download URLs.

The signature binds (run_id, name, expiry) under a server secret so a run cannot forge a
link for a different file or a longer TTL using only its bearer key. Reachability is the
separate per-run bearer (X-Run-Key); the signature is the 'what + how long' protection."""

from __future__ import annotations

import hmac
from hashlib import sha256
from urllib.parse import quote


def sign(secret: str, run_id: str, name: str, exp: int) -> str:
    msg = f"{run_id}:{name}:{exp}".encode()
    return hmac.new(secret.encode(), msg, sha256).hexdigest()


def verify(secret: str, run_id: str, name: str, exp: int, sig: str) -> bool:
    return hmac.compare_digest(sign(secret, run_id, name, exp), sig)


def signed_path(secret: str, run_id: str, name: str, exp: int, *, prefix: str = "") -> str:
    """Build the signed download path the agent follows VERBATIM.

    *prefix* is the app's router-mount prefix (e.g. ``/api`` — the router is include_router'd
    there in roles/boot); the caller supplies it so the returned URL is reachable as-is. The
    signature binds only (run_id, name, exp), so the prefix never affects verify()."""
    sig = sign(secret, run_id, name, exp)
    return f"{prefix}/runs/{run_id}/attachments/{quote(name)}?exp={exp}&sig={sig}"
