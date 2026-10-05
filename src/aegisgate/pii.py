"""PII round-trip: pseudonymize on the way in, restore on the way out.

The gateway's optional ``pii_mode=anonymize`` uses this to keep real
personally-identifiable data away from model providers: request messages are
rewritten to stable tokens (``Email_1``, ``Phone_2``, ``User_17``) before
routing, and the original values are restored in the response content before
it reaches the caller's UI. Because tokens are stored in a round-trip store,
the *same* original always maps to the *same* token across calls — model
outputs stay coherent and cached responses can be restored too.

Detection is deterministic (regex + Luhn + honorific/name heuristics):
emails, phone numbers, credit cards (Luhn-validated) and person names
(Title-case names adjacent to Mr/Ms/Mrs/Dr/Prof, plus a configurable
known-names list). The store is a Protocol — the in-memory LRU default is
swappable for a Redis-backed implementation (same seam pattern as the
rate-limit store) without touching callers.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Protocol

from pydantic import BaseModel, Field

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# NANP-ish: optional country code, 3-3-4 groups with -, . or space separators.
PHONE_RE = re.compile(
    r"(?<![\w@.-])(?:\+?\d{1,3}[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w.-])"
)

# 13-19 digits with optional separators; validated by Luhn afterwards.
CARD_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}\d(?!\d)")

HONORIFIC_NAME_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
)

TOKEN_RE = re.compile(r"\b((?:Email|Phone|Card|User)_\d+)\b")

# detection priority when spans overlap (cards before phones: a grouped card
# number can look phone-ish, never the reverse)
_KIND_PRIORITY = {"card": 0, "email": 1, "name": 2, "phone": 3}

REDACTION = "PII_REDACTED"


class PIIConfig(BaseModel):
    detect_emails: bool = True
    detect_phones: bool = True
    detect_cards: bool = True
    detect_names: bool = True
    known_names: list[str] = Field(default_factory=list)
    store_max_entries: int = 1024


class PIIHit(BaseModel):
    kind: str  # email | phone | card | name
    value: str
    start: int
    end: int


class PseudonymStore(Protocol):
    """Seam for the token<->original round-trip (in-memory LRU default;
    Redis-ready via the same store-swap pattern as the rate limiter)."""

    def get_token(self, kind: str, original: str) -> str: ...

    def get_original(self, token: str) -> str | None: ...


class InMemoryPseudonymStore:
    """LRU-bounded bidirectional map. Consistency guarantee: the same
    (kind, original) always yields the same token while it stays in the LRU."""

    def __init__(self, max_entries: int = 1024) -> None:
        self.max_entries = max_entries
        self._forward: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._reverse: OrderedDict[str, str] = OrderedDict()
        self._counters: dict[str, int] = {}

    def get_token(self, kind: str, original: str) -> str:
        key = (kind, original)
        if key in self._forward:
            self._forward.move_to_end(key)
            token = self._forward[key]
            self._reverse.move_to_end(token)
            return token
        self._counters[kind] = self._counters.get(kind, 0) + 1
        token = f"{kind}_{self._counters[kind]}"
        self._forward[key] = token
        self._reverse[token] = original
        self._evict()
        return token

    def get_original(self, token: str) -> str | None:
        if token in self._reverse:
            self._reverse.move_to_end(token)
            self._forward.move_to_end((token.split("_", 1)[0], self._reverse[token]))
            return self._reverse[token]
        return None

    def _evict(self) -> None:
        while len(self._forward) > self.max_entries:
            old_kind, old_original = next(iter(self._forward))
            old_token = self._forward.pop((old_kind, old_original))
            self._reverse.pop(old_token, None)


class PIIRoundTrip:
    """Detect -> pseudonymize -> restore, with cross-call token consistency."""

    def __init__(
        self, config: PIIConfig | None = None, store: PseudonymStore | None = None
    ) -> None:
        self.config = config or PIIConfig()
        self.store = store or InMemoryPseudonymStore(self.config.store_max_entries)

    # -- detection ------------------------------------------------------------
    def detect(self, text: str) -> list[PIIHit]:
        hits: list[PIIHit] = []
        cfg = self.config
        if cfg.detect_cards:
            for match in CARD_CANDIDATE_RE.finditer(text):
                digits = re.sub(r"[ -]", "", match.group())
                if 13 <= len(digits) <= 19 and _luhn_ok(digits):
                    hits.append(PIIHit(kind="card", value=match.group(),
                                       start=match.start(), end=match.end()))
        if cfg.detect_emails:
            hits.extend(PIIHit(kind="email", value=m.group(), start=m.start(), end=m.end())
                        for m in EMAIL_RE.finditer(text))
        if cfg.detect_phones:
            hits.extend(PIIHit(kind="phone", value=m.group(), start=m.start(), end=m.end())
                        for m in PHONE_RE.finditer(text))
        if cfg.detect_names:
            for m in HONORIFIC_NAME_RE.finditer(text):
                hits.append(PIIHit(kind="name", value=m.group(1),
                                   start=m.start(1), end=m.end(1)))
            for name in cfg.known_names:
                for m in re.finditer(re.escape(name), text):
                    hits.append(PIIHit(kind="name", value=name, start=m.start(), end=m.end()))
        return _drop_overlaps(hits)

    # -- round trip -------------------------------------------------------------
    def pseudonymize(self, text: str) -> tuple[str, dict[str, str]]:
        """Replace every detected PII value with a stable token.

        Returns ``(sanitized, map)`` where ``map`` is token -> original for
        this call. Tokens come from the round-trip store, so the same
        original maps to the same token across calls.
        """
        hits = self.detect(text)
        mapping: dict[str, str] = {}
        out: list[str] = []
        cursor = 0
        kind_prefix = {"email": "Email", "phone": "Phone", "card": "Card", "name": "User"}
        for hit in hits:
            out.append(text[cursor:hit.start])
            token = self.store.get_token(kind_prefix[hit.kind], hit.value)
            mapping[token] = hit.value
            out.append(token)
            cursor = hit.end
        out.append(text[cursor:])
        return "".join(out), mapping

    def restore(self, text: str, mapping: dict[str, str] | None = None) -> str:
        """Put originals back. Unknown tokens are left untouched."""

        def repl(match: re.Match[str]) -> str:
            token = match.group(1)
            original = (mapping or {}).get(token) or self.store.get_original(token)
            return original if original is not None else token

        return TOKEN_RE.sub(repl, text)


def _luhn_ok(digits: str) -> bool:
    total = 0
    alt = False
    for ch in reversed(digits):
        value = ord(ch) - 48
        if alt:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        alt = not alt
    return total % 10 == 0


def _drop_overlaps(hits: list[PIIHit]) -> list[PIIHit]:
    """Keep the highest-priority hit when spans overlap; otherwise leftmost."""
    ordered = sorted(hits, key=lambda h: (h.start, h.end, _KIND_PRIORITY.get(h.kind, 9)))
    kept: list[PIIHit] = []
    for hit in ordered:
        if kept and hit.start < kept[-1].end:
            prev = kept[-1]
            if _KIND_PRIORITY.get(hit.kind, 9) < _KIND_PRIORITY.get(prev.kind, 9):
                kept[-1] = hit
            continue
        kept.append(hit)
    return kept
