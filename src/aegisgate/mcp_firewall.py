"""The agent tool & MCP firewall — deterministic, offline tool-call inspection.

LLM gateways inspect chat completions; almost none inspect what the model does
*with* its tools. This module is AegisGate's wedge: a policy engine that sits
between a model's tool calls (OpenAI function calls, MCP ``tools/call``) and
the actual execution, and answers one question — *may this call run?*

Every layer is deterministic (regex + parsing — no network, no LLM, no DNS):

- argument schema validation (JSON-schema-lite: required keys, types, enums,
  numeric ranges)
- dangerous-command detection in string args, including base64-wrapped
  variants (decode + rescan)
- SSRF: URLs/hosts pointing at private ranges, loopback, cloud metadata,
  non-http(s) schemes, and localhost bypass encodings (0.0.0.0, ``[::1]``,
  decimal-IP). Host checks are *lexical* (stdlib ``ipaddress``) so the
  verdict is reproducible offline — a DNS-resolving variant belongs behind
  the same ``inspect_tool_call`` signature.
- path traversal: ``../``, null bytes, absolute paths outside allowlisted roots
- privilege-escalation verbs in tool names/args vs per-tool exemptions
- per-tool allowlist/denylist

Each rule carries a default *action* (``deny`` | ``sanitize`` | ``log_only``)
that operators can override per rule id: ``sanitize`` redacts the offending
argument value instead of blocking the call; ``log_only`` records the finding
and allows the call. Verdicts are the worst fired action
(deny > sanitize > allow).
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

Verdict = Literal["allow", "deny", "sanitize"]
RuleAction = Literal["deny", "sanitize", "log_only"]

REDACTED = "[aegisgate-redacted]"

# --- rule registry: id -> (default action, risk weight) ----------------------
DEFAULT_RULE_ACTIONS: dict[str, RuleAction] = {
    "tool_denied": "deny",
    "tool_not_allowlisted": "deny",
    "schema_missing_required": "deny",
    "schema_type_mismatch": "deny",
    "schema_enum_violation": "deny",
    "schema_range_violation": "deny",
    "dangerous_command": "deny",
    "dangerous_command_base64": "deny",
    "sql_write_blocked": "deny",
    "ssrf_scheme": "deny",
    "ssrf_private_host": "deny",
    "ssrf_metadata": "deny",
    "ssrf_localhost": "deny",
    "path_traversal": "deny",
    "null_byte_path": "deny",
    "path_outside_roots": "deny",
    "privilege_escalation": "deny",
    "dangerous_tool_name": "deny",
}

RULE_WEIGHTS: dict[str, int] = {
    "tool_denied": 40,
    "tool_not_allowlisted": 30,
    "schema_missing_required": 15,
    "schema_type_mismatch": 15,
    "schema_enum_violation": 15,
    "schema_range_violation": 10,
    "dangerous_command": 40,
    "dangerous_command_base64": 45,
    "sql_write_blocked": 30,
    "ssrf_scheme": 30,
    "ssrf_private_host": 35,
    "ssrf_metadata": 45,
    "ssrf_localhost": 30,
    "path_traversal": 30,
    "null_byte_path": 35,
    "path_outside_roots": 25,
    "privilege_escalation": 30,
    "dangerous_tool_name": 25,
}

# (id, compiled pattern, human label). All anchored to word boundaries where
# possible to keep false positives on ordinary prose low.
DANGEROUS_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "rm -rf / rm -fr",
        re.compile(r"\brm\s+(?:-{1,2}[A-Za-z]+\s+)*-\w*[rf]\b", re.I),
        "rm -rf",
    ),
    ("mkfs", re.compile(r"\bmkfs(?:\.\w+)?\b", re.I), "mkfs (filesystem reformat)"),
    (
        "drop table/db",
        re.compile(r"\bdrop\s+(table|database|schema|index|view)\b", re.I),
        "DROP TABLE/DATABASE",
    ),
    ("sudo", re.compile(r"\bsudo\b", re.I), "sudo (superuser execution)"),
    ("chmod 777", re.compile(r"\bchmod\s+777\b"), "chmod 777 (world-writable)"),
    (
        "curl|bash",
        re.compile(r"\b(?:curl|wget)\b[^;|&]*\|\s*(?:ba|z|da|k)?sh\b", re.I),
        "curl|wget piped to shell",
    ),
    (
        "fork bomb",
        re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        "bash fork bomb",
    ),
    ("shutdown", re.compile(r"\bshutdown\b", re.I), "shutdown"),
    (
        "registry edit",
        re.compile(r"\breg\s+(add|delete|import|load)\b", re.I),
        "Windows registry edit",
    ),
    ("regedit", re.compile(r"\bregedit\b", re.I), "regedit"),
]

SQL_WRITE_PATTERN = re.compile(
    r"\b(?:insert\s+into|delete\s+from|drop\s+|alter\s+table|truncate\s+table"
    r"|update\s+\w+\s+set|grant\b|revoke\b)\b",
    re.I,
)

PRIV_ESC_TOOL_NAME = re.compile(
    r"(?:delete_all|\bdrop\b|\bgrant\b|admin|impersonate|escalate)", re.I
)
PRIV_ESC_VALUE = re.compile(
    r"(?:delete_all|impersonate|grant_admin|escalate_privileges|set_admin|make_admin)", re.I
)
PRIV_ESC_ARG_KEY = re.compile(r"(?:is_admin|as_admin|sudo|impersonate)", re.I)

BASE64_CANDIDATE = re.compile(r"^[A-Za-z0-9+/_-]{8,}={0,2}$")

HOST_ARG_KEYS = {"url", "host", "hostname", "endpoint", "target", "addr", "address",
                 "origin", "base_url", "api_url", "webhook", "callback", "link"}
PATH_ARG_KEYS = {"path", "file", "filename", "filepath", "file_path", "dir", "directory",
                 "folder", "root", "output", "output_path", "input", "input_path",
                 "template", "include", "source", "dest", "destination"}
SCHEME_URL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
BARE_HOST = re.compile(r"^[A-Za-z0-9._\-]+(?::\d+)?(?:/.*)?$")
LOCALHOST_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


class DecisionReason(BaseModel):
    """One fired rule: what, where, and with which action."""

    id: str
    layer: str
    message: str
    action: RuleAction


class ToolCallDecision(BaseModel):
    """Result of inspecting one tool call."""

    tool_name: str
    verdict: Verdict = "allow"
    reasons: list[DecisionReason] = Field(default_factory=list)
    sanitized_args: dict[str, Any] = Field(default_factory=dict)
    risk: int = 0  # 0-100 informational score: sum of fired rule weights, capped

    def explain(self) -> str:
        """Human-readable justification for this decision."""
        if not self.reasons:
            return (
                f"Tool '{self.tool_name}' allowed: no policy rules matched "
                f"(risk {self.risk}/100)."
            )
        head = f"Tool '{self.tool_name}' verdict={self.verdict.upper()} (risk {self.risk}/100)."
        lines = [
            f"- [{r.action}] {r.id} ({r.layer}): {r.message}" for r in self.reasons
        ]
        return "\n".join([head, *lines])


class FirewallConfig(BaseModel):
    """Toggleable policy layers + per-tool rules.

    ``actions`` overrides the default action per rule id
    (e.g. ``{"dangerous_command": "sanitize"}`` redacts the matched argument
    instead of denying the call).
    """

    # layer toggles
    schema_validation: bool = True
    dangerous_commands: bool = True
    base64_rescan: bool = True
    ssrf: bool = True
    path_traversal: bool = True
    privilege_escalation: bool = True
    allowlist_denylist: bool = True

    # per-rule action overrides
    actions: dict[str, str] = Field(default_factory=dict)

    # layer (f): per-tool allowlist/denylist
    tool_allowlist: list[str] = Field(default_factory=list)
    tool_denylist: list[str] = Field(default_factory=list)

    # layer (a): per-tool JSON-schema-lite (required keys, types, enums, ranges)
    tool_schemas: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # per-tool extras: allowed_roots, sql_read_only, allowed_schemes
    tool_settings: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # layer (c)/(d) globals
    allowed_schemes: list[str] = Field(default_factory=lambda: ["http", "https"])
    global_allowed_roots: list[str] = Field(default_factory=list)

    # layer (e): tools exempt from the privilege-escalation name check
    priv_esc_tool_allowlist: list[str] = Field(default_factory=list)

    def action_for(self, rule_id: str) -> RuleAction:
        raw = self.actions.get(rule_id, DEFAULT_RULE_ACTIONS.get(rule_id, "deny"))
        if raw not in ("deny", "sanitize", "log_only"):  # defensive: bad policy value
            return "deny"
        return raw  # type: ignore[return-value]


class ToolFirewall:
    """Deterministic inspector for model-issued tool calls (OpenAI tools, MCP)."""

    def __init__(self, config: FirewallConfig | None = None) -> None:
        self.config = config or FirewallConfig()

    # -- public API ------------------------------------------------------------
    def inspect_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ToolCallDecision:
        """Inspect one call. Pure function of (config, tool_name, arguments):
        no network, no DNS, no clock, no randomness."""
        cfg = self.config
        findings: list[_Finding] = []
        sanitized = _deep_copy_json(arguments)

        # layer (f): per-tool allowlist/denylist
        if cfg.allowlist_denylist:
            if tool_name in cfg.tool_denylist:
                findings.append(_Finding("tool_denied", "allowlist", path=None, value=None,
                                         message=f"tool '{tool_name}' is denylisted"))
            elif cfg.tool_allowlist and tool_name not in cfg.tool_allowlist:
                findings.append(_Finding("tool_not_allowlisted", "allowlist", path=None, value=None,
                                         message=f"tool '{tool_name}' is not in the allowlist"))

        # layer (e): privilege-escalation verbs in the tool name
        if cfg.privilege_escalation and tool_name not in cfg.priv_esc_tool_allowlist:
            if PRIV_ESC_TOOL_NAME.search(tool_name):
                findings.append(_Finding("dangerous_tool_name", "privilege_escalation",
                                         path=None, value=None,
                                         message=f"tool name '{tool_name}' matches a "
                                         f"privilege-escalation verb"))

        # layer (a): argument schema validation
        if cfg.schema_validation:
            self._check_schema(tool_name, arguments, findings)

        # layers (b)/(c)/(d)/(e): scan every string leaf of the argument tree
        for path, value, key_hint in _walk_strings(arguments):
            if not isinstance(value, str) or not value:
                continue
            self._scan_string(tool_name, key_hint, path, value, sanitized, findings)

        # layer (e): privilege-escalating argument KEYS (any value type)
        if cfg.privilege_escalation:
            for path, key in _walk_keys(arguments):
                if PRIV_ESC_ARG_KEY.search(key):
                    findings.append(_Finding("privilege_escalation", "privilege_escalation",
                                             path=path, value=None,
                                             message=f"argument key '{key}' requests a "
                                             f"privilege-escalating operation"))

        return self._decide(tool_name, arguments, sanitized, findings)

    def explain(self, decision: ToolCallDecision) -> str:
        """Human-readable justification for a decision."""
        return decision.explain()

    # -- layers ------------------------------------------------------------------
    def _check_schema(
        self, tool_name: str, arguments: dict[str, Any], findings: list[_Finding]
    ) -> None:
        schema = self.config.tool_schemas.get(tool_name)
        if not schema:
            return
        for required in schema.get("required", []):
            if required not in arguments:
                findings.append(_Finding("schema_missing_required", "schema", path=None, value=None,
                                         message=f"missing required argument '{required}'"))
        properties = schema.get("properties", {})
        for key, value in arguments.items():
            spec = properties.get(key)
            if not isinstance(spec, dict):
                continue  # JSON-schema-lite: unspecified keys are permitted
            expected = spec.get("type")
            if expected and not _type_ok(value, expected):
                findings.append(_Finding("schema_type_mismatch", "schema", path=[key], value=None,
                                         message=f"argument '{key}' should be {expected}, "
                                         f"got {type(value).__name__}"))
                continue
            if "enum" in spec and value not in spec["enum"]:
                findings.append(_Finding("schema_enum_violation", "schema", path=[key], value=None,
                                         message=f"argument '{key}'={value!r} not in "
                                         f"enum {spec['enum']}"))
            for bound, op in (("minimum", "<"), ("maximum", ">")):
                is_num = isinstance(value, (int, float)) and not isinstance(value, bool)
                if bound in spec and is_num:
                    if (op == "<" and value < spec[bound]) or (op == ">" and value > spec[bound]):
                        findings.append(_Finding("schema_range_violation", "schema", path=[key],
                                                 value=None,
                                                 message=f"argument '{key}'={value} violates "
                                                 f"{bound}={spec[bound]}"))

    def _scan_string(
        self,
        tool_name: str,
        key_hint: str,
        path: list[str | int],
        value: str,
        sanitized: dict[str, Any],
        findings: list[_Finding],
    ) -> None:
        cfg = self.config
        tool_cfg = cfg.tool_settings.get(tool_name, {})

        # layer (b): dangerous commands, optionally base64-wrapped
        if cfg.dangerous_commands:
            for label, pattern, human in DANGEROUS_PATTERNS:
                if pattern.search(value):
                    findings.append(_Finding("dangerous_command", "dangerous_commands", path=path,
                                             value=value,
                                             message=f"argument {'.'.join(map(str, path))!r} "
                                             f"contains dangerous pattern "
                                             f"'{label}' ({human})"))
            if cfg.base64_rescan:
                hit_label = _base64_rescan(value)
                if hit_label is not None:
                    findings.append(_Finding(
                        "dangerous_command_base64", "dangerous_commands", path=path,
                        value=value,
                        message=f"argument {'.'.join(map(str, path))!r} carries a base64-encoded "
                        f"dangerous payload ('{hit_label[0]}', {hit_label[1]})"))

        # per-tool read-only SQL: write verbs in any string arg of the tool
        if tool_cfg.get("sql_read_only") and SQL_WRITE_PATTERN.search(value):
            findings.append(_Finding("sql_write_blocked", "dangerous_commands", path=path,
                                     value=value,
                                     message=f"tool '{tool_name}' is read-only; argument "
                                             f"{'.'.join(map(str, path))!r} contains a SQL "
                                             f"write verb"))

        # layer (c): SSRF
        if cfg.ssrf:
            self._check_ssrf(tool_name, key_hint, path, value, findings)

        # layer (d): path traversal
        if cfg.path_traversal:
            self._check_path(tool_name, key_hint, path, value, findings)

        # layer (e): privilege-escalation verbs in arg values
        if cfg.privilege_escalation and PRIV_ESC_VALUE.search(value):
            findings.append(_Finding("privilege_escalation", "privilege_escalation", path=path,
                                     value=value,
                                     message=f"argument {'.'.join(map(str, path))!r} contains "
                                     f"a privilege-escalation verb"))

    def _check_ssrf(
        self, tool_name: str, key_hint: str, path: list[str | int], value: str,
        findings: list[_Finding],
    ) -> None:
        allowed = self.config.tool_settings.get(tool_name, {}).get(
            "allowed_schemes", self.config.allowed_schemes
        )
        host = None
        if SCHEME_URL.match(value):
            scheme, _, rest = value.partition("://")
            if scheme.lower() not in [s.lower() for s in allowed]:
                findings.append(_Finding("ssrf_scheme", "ssrf", path=path, value=value,
                                         message=f"scheme '{scheme}:' is not allowed "
                                         f"(allowed: {', '.join(allowed)})"))
                # no early return: keep checking the host too (defense in depth)
            rest = rest.split("/", 1)[0]
            host = rest.rsplit("@", 1)[-1]  # strip userinfo
        elif key_hint in HOST_ARG_KEYS and BARE_HOST.match(value):
            host = value.split("/", 1)[0]
        if not host:
            return
        if host.startswith("[") and "]" in host:  # [::1] or [::1]:8080
            host = host[1:host.index("]")]
        elif host.count(":") > 1:  # bare IPv6 literal (::1) — no port to strip
            pass
        elif ":" in host:  # hostname:port
            host = host.rsplit(":", 1)[0]
        host = host.strip("[]").lower()
        if not host:
            return
        if host in LOCALHOST_NAMES:
            findings.append(_Finding("ssrf_localhost", "ssrf", path=path, value=value,
                                     message=f"host '{host}' is loopback by name"))
            return
        if host in {"169.254.169.254", "metadata.google.internal", "100.100.100.200"}:
            findings.append(_Finding("ssrf_metadata", "ssrf", path=path, value=value,
                                     message=f"host '{host}' is a cloud metadata endpoint"))
            return
        ip = _as_ip(host)
        if ip is not None and _is_private_ip(ip):
            findings.append(_Finding("ssrf_private_host", "ssrf", path=path, value=value,
                                     message=f"host '{host}' resolves (lexically) to a private/"
                                     f"loopback range ({ip})"))

    def _check_path(
        self, tool_name: str, key_hint: str, path: list[str | int], value: str,
        findings: list[_Finding],
    ) -> None:
        if "\x00" in value:
            findings.append(_Finding("null_byte_path", "path_traversal", path=path, value=value,
                                     message=f"argument {'.'.join(map(str, path))!r} contains a "
                                     f"null byte"))
            return
        if "../" in value or "..\\" in value:
            findings.append(_Finding("path_traversal", "path_traversal", path=path, value=value,
                                     message=f"argument {'.'.join(map(str, path))!r} contains a "
                                             f"parent-directory traversal ('..')"))
            return
        roots = self.config.tool_settings.get(tool_name, {}).get(
            "allowed_roots", self.config.global_allowed_roots
        )
        is_abs = value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value)
        if roots and is_abs and key_hint in PATH_ARG_KEYS and not _under_any_root(value, roots):
            findings.append(_Finding("path_outside_roots", "path_traversal", path=path, value=value,
                                     message=f"absolute path '{value}' is outside allowed roots "
                                     f"{roots}"))

    # -- verdict -----------------------------------------------------------------
    def _decide(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        sanitized: dict[str, Any],
        findings: list[_Finding],
    ) -> ToolCallDecision:
        reasons: list[DecisionReason] = []
        risk = 0
        verdict: Verdict = "allow"
        for f in findings:
            action = self.config.action_for(f.id)
            if action == "sanitize" and f.path is not None:
                _redact(sanitized, f.path)
            elif action == "sanitize":
                action = "deny"  # absence (e.g. missing required arg) cannot be redacted
            reasons.append(DecisionReason(id=f.id, layer=f.layer, message=f.message,
                                          action=action))
            risk += RULE_WEIGHTS.get(f.id, 10)
            if action == "deny":
                verdict = "deny"
            elif action == "sanitize" and verdict != "deny":
                verdict = "sanitize"
        return ToolCallDecision(
            tool_name=tool_name,
            verdict=verdict,
            reasons=reasons,
            sanitized_args=sanitized if verdict != "deny" else dict(arguments),
            risk=min(100, risk),
        )


# --- internal helpers (module-level, deterministic) -----------------------------


class _Finding:
    __slots__ = ("id", "layer", "path", "value", "message")

    def __init__(self, id: str, layer: str, *, path: list[str | int] | None,
                 value: str | None, message: str) -> None:
        self.id = id
        self.layer = layer
        self.path = path
        self.value = value
        self.message = message


def _walk_strings(
    node: Any, path: list[str | int] | None = None, key_hint: str = ""
):
    """Yield (path, value, key_hint) for every string leaf; key_hint is the
    nearest dict key (used to gate host/path checks to relevant args)."""
    path = path or []
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_strings(value, [*path, key], str(key).lower())
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_strings(value, [*path, i], key_hint)
    elif isinstance(node, str):
        yield path, node, key_hint


def _walk_keys(node: Any, path: list[str | int] | None = None):
    """Yield (path_to_value, key) for every dict key in the argument tree."""
    path = path or []
    if isinstance(node, dict):
        for key, value in node.items():
            yield [*path, key], str(key)
            yield from _walk_keys(value, [*path, key])
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_keys(value, [*path, i])


def _deep_copy_json(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _deep_copy_json(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_deep_copy_json(v) for v in node]
    return node


def _redact(container: Any, path: list[str | int]) -> None:
    """Replace the value at ``path`` inside ``container`` with the redaction token."""
    if not path:
        return
    node = container
    for key in path[:-1]:
        node = node[key] if not isinstance(node, list) else node[int(key)]  # type: ignore[index]
    last = path[-1]
    if isinstance(node, dict):
        node[str(last)] = REDACTED
    elif isinstance(node, list):
        node[int(last)] = REDACTED


def _type_ok(value: Any, expected: str) -> bool:
    checks = {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
        "null": lambda v: v is None,
    }
    return checks.get(expected, lambda _v: True)(value)


def _base64_rescan(value: str) -> tuple[str, str] | None:
    """Decode+rescan base64 payloads: the whole string when it looks encoded,
    otherwise any embedded base64 token (e.g. 'echo <b64> | base64 -d | sh').
    Returns the matched (label, human) pattern pair, or None."""
    candidates = [value.strip()]
    candidates.extend(re.findall(r"[A-Za-z0-9+/_-]{8,}={0,2}", value))
    for candidate in candidates:
        if not BASE64_CANDIDATE.match(candidate):
            continue
        decoded = _try_b64decode(candidate)
        if not decoded:
            continue
        for label, pattern, human in DANGEROUS_PATTERNS:
            if pattern.search(decoded):
                return label, human
    return None


def _try_b64decode(value: str) -> str | None:
    for decode in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = decode(value + "=" * (-len(value) % 4))
            text = raw.decode("utf-8")
            if text.isprintable() or "\n" in text:
                return text
            return None
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
    return None


def _as_ip(host: str) -> str | None:
    """Normalize encodings (decimal int, hex) to a dotted IP when possible."""
    if host.isdigit() and 8 <= len(host) <= 10:
        n = int(host)
        if n < 2**32:
            return ".".join(str((n >> shift) & 0xFF) for shift in (24, 16, 8, 0))
        return None
    if host.startswith("0x") and len(host) <= 10:
        try:
            n = int(host, 16)
            if n < 2**32:
                return ".".join(str((n >> shift) & 0xFF) for shift in (24, 16, 8, 0))
        except ValueError:
            return None
    return host


def _is_private_ip(dotted: str) -> bool:
    try:
        ip = ipaddress.ip_address(dotted)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_reserved
    )


def _under_any_root(path: str, roots: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    for root in roots:
        root_norm = root.replace("\\", "/").rstrip("/")
        if normalized == root_norm or normalized.startswith(root_norm + "/"):
            return True
    return False


def load_policy(path: str) -> FirewallConfig:
    """Load a YAML policy file into a :class:`FirewallConfig`.

    Expected shape (see ``policies/example_policy.yaml``)::

        rules:            # global layer toggles
          dangerous_commands: true
        tool_allowlist: [...]
        tool_denylist: [...]
        allowed_schemes: [https]
        allowed_roots: [...]
        actions: {dangerous_command: sanitize}
        tools:
          run_query:
            schema: {required: [sql], properties: {...}}
            sql_read_only: true
          browse_web: {allowed_schemes: [https]}
          run_terminal: {allowed_roots: ["/workspace"]}
    """
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"policy file {path!r} must contain a YAML mapping")
    rules = data.get("rules", {}) or {}
    tools = data.get("tools", {}) or {}
    tool_schemas: dict[str, dict[str, Any]] = {}
    tool_settings: dict[str, dict[str, Any]] = {}
    for name, spec in tools.items():
        if not isinstance(spec, dict):
            continue
        if "schema" in spec:
            tool_schemas[name] = spec["schema"]
        extras = {k: v for k, v in spec.items()
                  if k in {"allowed_roots", "sql_read_only", "allowed_schemes"}}
        if extras:
            tool_settings[name] = extras
    return FirewallConfig(
        schema_validation=bool(rules.get("schema_validation", True)),
        dangerous_commands=bool(rules.get("dangerous_commands", True)),
        base64_rescan=bool(rules.get("base64_rescan", True)),
        ssrf=bool(rules.get("ssrf", True)),
        path_traversal=bool(rules.get("path_traversal", True)),
        privilege_escalation=bool(rules.get("privilege_escalation", True)),
        allowlist_denylist=bool(rules.get("allowlist_denylist", True)),
        actions=data.get("actions", {}) or {},
        tool_allowlist=list(data.get("tool_allowlist", []) or []),
        tool_denylist=list(data.get("tool_denylist", []) or []),
        tool_schemas=tool_schemas,
        tool_settings=tool_settings,
        allowed_schemes=list(data.get("allowed_schemes", ["http", "https"]) or ["http", "https"]),
        global_allowed_roots=list(data.get("allowed_roots", []) or []),
        priv_esc_tool_allowlist=list(data.get("priv_esc_tool_allowlist", []) or []),
    )
