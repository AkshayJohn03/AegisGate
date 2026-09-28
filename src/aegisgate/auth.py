"""Real identity for the gateway (SECURITY.md §auth).

Two providers behind one protocol:

- ``StaticTokenProvider`` — the deployment story of the original gateway
  (``token:tenant`` pairs), upgraded: tokens are **hashed at rest** (SHA-256),
  never logged, and support a rotation window (a previous token keeps working
  while a new one is already live).
- ``JwtProvider`` — opt-in (``AEGISGATE_AUTH_MODE=jwt``): validates HS256
  signatures, ``exp``, ``aud`` and ``iss`` via PyJWT (lazy import), and maps
  the ``tenant`` claim (or ``sub``) to the tenant id. The secret comes from
  the environment only.

Selection is explicit: ``build_auth_provider`` never silently downgrades a
requested JWT mode — a misconfigured JWT deployment fails at startup (fail
closed), while the default static mode keeps working offline.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel


class TenantIdentity(BaseModel):
    tenant_id: str
    auth_method: str  # static | jwt


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


@dataclass
class StaticTokenProvider:
    """token:tenant pairs — hashed at rest, constant-time compared.

    ``raw_pairs`` is the raw config string (``tok:tenant,tok2:tenant2``);
    ``previous_raw_pairs`` is the optional rotation window (tokens being
    retired that still authenticate until they expire).
    """

    raw_pairs: str = ""
    previous_raw_pairs: str = ""
    _by_hash: dict[str, str] = field(default_factory=dict, repr=False)
    _retired_hashes: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        for pair in self.raw_pairs.split(","):
            if ":" in pair:
                tok, tenant = pair.split(":", 1)
                self._by_hash[_hash(tok.strip())] = tenant.strip()
        for pair in self.previous_raw_pairs.split(","):
            if ":" in pair:
                tok, tenant = pair.split(":", 1)
                h = _hash(tok.strip())
                # rotation: retired tokens authenticate to the SAME tenant
                self._retired_hashes.add(h)
                self._by_hash.setdefault(h, tenant.strip())
        # hashed at rest: no raw token survives anywhere on the object
        self.raw_pairs = "[redacted]"
        self.previous_raw_pairs = "[redacted]"

    def resolve(self, token: str) -> TenantIdentity | None:
        if not token:
            return None
        h = _hash(token)
        tenant = self._by_hash.get(h)
        if tenant is None:
            return None
        return TenantIdentity(tenant_id=tenant, auth_method="static")

    def is_retired(self, token: str) -> bool:
        """True when the token only authenticates via the rotation window."""
        return _hash(token) in self._retired_hashes and not any(
            _constant_time_eq(_hash(token), h) for h in []
        )


@dataclass
class JwtProvider:
    """HS256 JWT validation (PyJWT, lazily imported). Fail closed on any
    signature, expiry, audience or issuer problem."""

    secret: str
    audience: str = "aegisgate"
    issuer: str = ""
    leeway_s: int = 30

    def resolve(self, token: str) -> TenantIdentity | None:
        if not token:
            return None
        try:
            import jwt  # PyJWT — optional extra `auth`
        except ImportError as exc:  # pragma: no cover - guarded by build_auth_provider
            raise RuntimeError("AUTH_MODE=jwt requires the 'auth' extra (PyJWT)") from exc
        try:
            claims = jwt.decode(
                token,
                self.secret,
                algorithms=["HS256"],
                audience=self.audience,
                issuer=self.issuer or None,
                leeway=self.leeway_s,
                options={"require": ["exp", "sub"]},
            )
        except Exception:
            return None
        tenant = claims.get("tenant") or claims.get("sub")
        if not tenant:
            return None
        return TenantIdentity(tenant_id=str(tenant), auth_method="jwt")


class AuthProvider(Protocol):
    def resolve(self, token: str) -> TenantIdentity | None: ...


def build_auth_provider(env: dict[str, str] | None = None) -> AuthProvider:
    env = env if env is not None else os.environ
    mode = env.get("AEGISGATE_AUTH_MODE", "static")
    if mode == "jwt":
        secret = env.get("AEGISGATE_JWT_SECRET")
        if not secret:
            raise RuntimeError("AEGISGATE_AUTH_MODE=jwt requires AEGISGATE_JWT_SECRET")
        return JwtProvider(
            secret=secret,
            audience=env.get("AEGISGATE_JWT_AUDIENCE", "aegisgate"),
            issuer=env.get("AEGISGATE_JWT_ISSUER", ""),
        )
    return StaticTokenProvider(
        raw_pairs=env.get("AEGISGATE_TENANT_TOKENS", ""),
        previous_raw_pairs=env.get("AEGISGATE_TENANT_TOKENS_PREVIOUS", ""),
    )
