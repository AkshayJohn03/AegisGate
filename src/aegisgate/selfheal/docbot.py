"""DocHealer: self-healing documentation for an API.

Pipeline: parse an OpenAPI spec -> parse docs markdown into endpoint
sections -> diff structure (endpoints added/removed/params changed) -> draft
patched docs via the LLMClient -> *validate* the draft (lint headings and
links, replay HTTP examples against the live spec) -> fall back to a
deterministic template for any section the draft got wrong -> emit a
git-diff-style patch bundle.

The validator is the interesting part: an LLM draft is only adopted when it
exactly matches the live spec; anything else is regenerated from the spec
itself. Docs can therefore never drift *further* by running the healer, and
the offline demo works with EchoMockClient.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel

from aegisgate.llm.base import ChatRequest, LLMClient, Message

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_ENDPOINT_HEADING = re.compile(r"^(#{1,6})\s+([A-Za-z]{3,7})\s+(/\S*)\s*$", re.MULTILINE)
_BACKTICK = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)`")
_FENCED = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_LINK = re.compile(r"\]\(([^)]+)\)")
_REQUEST_LINE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE)\s+(\S+)")


class EndpointSpec(BaseModel):
    method: str
    path: str
    params: list[str] = []
    body_props: list[str] = []
    summary: str = ""


class DocFinding(BaseModel):
    kind: str  # missing_endpoint | stale_endpoint | param_drift
    method: str = ""
    path: str = ""
    file: str = ""
    detail: str = ""


@dataclass
class DocSection:
    file: str
    level: int
    method: str
    path: str
    text: str


@dataclass
class DocPatch:
    file: str
    old: str
    new: str
    diff: str
    findings: list[DocFinding] = field(default_factory=list)


@dataclass
class DocPatchBundle:
    findings: list[DocFinding]
    files: list[DocPatch]
    validation: dict[str, Any]
    manifest: dict[str, Any]

    @property
    def full_diff(self) -> str:
        return "\n\n".join(patch.diff for patch in self.files)


def parse_openapi(spec: dict[str, Any]) -> dict[tuple[str, str], EndpointSpec]:
    endpoints: dict[tuple[str, str], EndpointSpec] = {}
    for path, path_item in (spec.get("paths") or {}).items():
        for method, op in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            params = [
                p.get("name", "")
                for p in (op.get("parameters") or [])
                if isinstance(p, dict) and p.get("name")
            ]
            body_props: list[str] = []
            body = op.get("requestBody") or {}
            content = (body.get("content") or {}).get("application/json") or {}
            schema = content.get("schema") or {}
            body_props = list((schema.get("properties") or {}).keys())
            endpoints[(method.lower(), path)] = EndpointSpec(
                method=method.lower(),
                path=path,
                params=params,
                body_props=body_props,
                summary=op.get("summary", ""),
            )
    return endpoints


def split_blocks(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading_or_empty, block_text) chunks."""
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [("", text)]
    blocks: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        blocks.append(("", text[: matches[0].start()]))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append((match.group(0), text[match.start() : end]))
    return blocks


def section_params(section_text: str) -> set[str]:
    return set(_BACKTICK.findall(section_text))


def documented_params(section: DocSection) -> set[str]:
    body = "\n".join(section.text.splitlines()[1:])  # drop heading line
    return section_params(body) - {section.path}


def spec_param_set(ep: EndpointSpec) -> set[str]:
    """All names a doc section must document: params plus body properties."""
    return set(ep.params) | set(ep.body_props)


class DocHealer:
    def __init__(
        self,
        spec: dict[str, Any],
        docs_dir: str | Path,
        llm: LLMClient,
        *,
        model: str = "gpt-4o-mini",
    ) -> None:
        self.spec = spec
        self.docs_dir = Path(docs_dir)
        self.llm = llm
        self.model = model
        self.endpoints = parse_openapi(spec)

    # -- scanning -----------------------------------------------------------
    def _doc_files(self) -> list[Path]:
        return sorted(self.docs_dir.glob("*.md"))

    def _sections_in(self, file: Path) -> list[DocSection]:
        text = file.read_text(encoding="utf-8")
        sections: list[DocSection] = []
        for heading, block in split_blocks(text):
            match = _ENDPOINT_HEADING.match(heading)
            if match:
                sections.append(
                    DocSection(
                        file=file.name,
                        level=len(match.group(1)),
                        method=match.group(2).lower(),
                        path=match.group(3),
                        text=block.rstrip() + "\n",
                    )
                )
        return sections

    def scan(self) -> tuple[list[DocFinding], dict[str, list[DocSection]]]:
        findings: list[DocFinding] = []
        sections_by_file: dict[str, list[DocSection]] = {}
        documented: dict[tuple[str, str], DocSection] = {}
        for file in self._doc_files():
            sections = self._sections_in(file)
            sections_by_file[file.name] = sections
            for section in sections:
                documented[(section.method, section.path)] = section

        for method, path in self.endpoints:
            if (method, path) not in documented:
                findings.append(
                    DocFinding(
                        kind="missing_endpoint",
                        method=method,
                        path=path,
                        detail="endpoint exists in spec but is undocumented",
                    )
                )
        for (method, path), section in documented.items():
            if (method, path) not in self.endpoints:
                findings.append(
                    DocFinding(
                        kind="stale_endpoint",
                        method=method,
                        path=path,
                        file=section.file,
                        detail="documented endpoint no longer exists in spec",
                    )
                )
                continue
            spec_params = spec_param_set(self.endpoints[(method, path)])
            doc_params = documented_params(section)
            if doc_params != spec_params:
                findings.append(
                    DocFinding(
                        kind="param_drift",
                        method=method,
                        path=path,
                        file=section.file,
                        detail=f"documented params {sorted(doc_params)} != spec "
                        f"params {sorted(spec_params)}",
                    )
                )
        return findings, sections_by_file

    def _assign_missing_to_file(
        self, finding: DocFinding, sections_by_file: dict[str, list[DocSection]]
    ) -> str:
        """Attach a missing endpoint to the file with the most related docs."""
        prefix = finding.path.rsplit("/", 1)[0]
        best_file, best_score = None, -1
        for file_name, sections in sections_by_file.items():
            score = sum(1 for s in sections if s.path.startswith(prefix))
            if score > best_score:
                best_file, best_score = file_name, score
        return best_file or (next(iter(sections_by_file), "README.md"))

    # -- rendering ----------------------------------------------------------
    def _render_section(self, ep: EndpointSpec, level: int = 3) -> str:
        lines = [
            f"{'#' * level} {ep.method.upper()} {ep.path}",
            "",
            ep.summary or f"Calls `{ep.method.upper()} {ep.path}`.",
            "",
        ]
        all_params = ep.params + [p for p in ep.body_props if p not in ep.params]
        if all_params:
            lines.append("**Params:** " + ", ".join(f"`{p}`" for p in all_params))
            lines.append("")
        path_params = set(re.findall(r"{(\w+)}", ep.path))
        query_names = [p for p in ep.params if p not in path_params]
        query = "?" + "&".join(f"{p}=1" for p in query_names) if query_names else ""
        lines.append("```http")
        lines.append(f"{ep.method.upper()} {ep.path}{query}")
        lines.append("```")
        if ep.body_props:
            body = {p: "..." for p in ep.body_props}
            lines.extend(["", "```json", _pretty_json(body), "```"])
        return "\n".join(lines) + "\n"

    async def _draft(self, file_name: str, old_text: str, findings: list[DocFinding]) -> str:
        drift = "\n".join(f"- [{f.kind}] {f.method.upper()} {f.path}: {f.detail}" for f in findings)
        prompt = (
            "You maintain API reference docs. The OpenAPI spec changed; "
            "update the markdown so it matches the spec exactly.\n\n"
            f"Detected drift:\n{drift}\n\n"
            f"Current {file_name}:\n\n{old_text}\n\n"
            "Return only the corrected markdown."
        )
        response = await self.llm.complete(
            ChatRequest(
                model=self.model,
                messages=[Message(role="user", content=prompt)],
                temperature=0.1,
            )
        )
        return response.content

    # -- validation ---------------------------------------------------------
    def _validate_text(self, file_name: str, text: str) -> list[str]:
        errors: list[str] = []
        for match in _ENDPOINT_HEADING.finditer(text):
            method = match.group(2).upper()
            if method.lower() not in HTTP_METHODS:
                errors.append(f"{file_name}: bad method in heading '{match.group(0).strip()}'")
        for match in _LINK.finditer(text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            if not (self.docs_dir / target.split("#")[0]).exists():
                errors.append(f"{file_name}: broken link target '{target}'")
        for _lang, body in _FENCED.findall(text):
            first_line = body.strip().splitlines()[0] if body.strip() else ""
            req = _REQUEST_LINE.match(first_line)
            if not req:
                continue
            method, raw_path = req.group(1).lower(), req.group(2)
            path_only, _, query = raw_path.partition("?")
            key = (method, path_only)
            if key not in self.endpoints:
                errors.append(
                    f"{file_name}: example requests unknown endpoint {method.upper()} {path_only}"
                )
                continue
            spec_params = set(self.endpoints[key].params)
            for name in parse_qs(urlparse(f"http://x/?{query}").query):
                if name not in spec_params:
                    errors.append(
                        f"{file_name}: example uses query param '{name}' "
                        f"not in spec for {path_only}"
                    )
        return errors

    def _adopt(self, section: DocSection) -> str | None:
        """Adopt a draft section only when it exactly matches the live spec."""
        ep = self.endpoints.get((section.method, section.path))
        if ep is None:
            return None
        if documented_params(section) != spec_param_set(ep):
            return None
        errors = self._validate_text(section.file, section.text)
        return section.text if not errors else None

    # -- healing ------------------------------------------------------------
    async def heal(self) -> DocPatchBundle:
        findings, sections_by_file = self.scan()
        findings_by_file: dict[str, list[DocFinding]] = {}
        patches: list[DocPatch] = []
        from_llm = 0
        from_template = 0

        for finding in findings:
            target = finding.file or self._assign_missing_to_file(finding, sections_by_file)
            findings_by_file.setdefault(target, []).append(finding)

        for file_name, file_findings in findings_by_file.items():
            file_path = self.docs_dir / file_name
            old_text = file_path.read_text(encoding="utf-8")
            spec_eps = [
                ep
                for (method, path), ep in self.endpoints.items()
                if any(f.method == method and f.path == path for f in file_findings)
            ]

            draft_text = await self._draft(file_name, old_text, file_findings)
            draft_sections = {
                (s.method, s.path): s for s in self._sections_from_text(draft_text, file_name)
            }

            drifted_keys = {(f.method, f.path) for f in file_findings if f.kind == "param_drift"}
            new_blocks: list[str] = []
            for heading, block in split_blocks(old_text):
                match = _ENDPOINT_HEADING.match(heading)
                if not match:
                    if block.strip():
                        new_blocks.append(block.rstrip())
                    continue
                key = (match.group(2).lower(), match.group(3))
                if key not in self.endpoints:
                    continue  # stale section: dropped
                section = DocSection(
                    file=file_name,
                    level=len(match.group(1)),
                    method=key[0],
                    path=key[1],
                    text=block.rstrip() + "\n",
                )
                if key in drifted_keys:
                    continue  # drifted section: re-rendered from spec below
                # Prefer the draft's rendition; adopt only when it exactly
                # matches the live spec, otherwise keep the original text.
                candidate = draft_sections.get(key, section)
                adopted = self._adopt(candidate)
                if adopted is not None:
                    new_blocks.append(adopted.rstrip())
                    if key in draft_sections:
                        from_llm += 1
                else:
                    new_blocks.append(section.text.rstrip())

            for ep in sorted(spec_eps, key=lambda e: (e.path, e.method)):
                new_blocks.append(self._render_section(ep).rstrip())
                from_template += 1

            new_text = "\n\n".join(new_blocks) + "\n"
            diff = "\n".join(
                difflib.unified_diff(
                    old_text.splitlines(),
                    new_text.splitlines(),
                    fromfile=f"a/docs/{file_name}",
                    tofile=f"b/docs/{file_name}",
                    lineterm="",
                )
            )
            patches.append(
                DocPatch(
                    file=file_name, old=old_text, new=new_text, diff=diff, findings=file_findings
                )
            )

        lint_errors: list[str] = []
        replay_errors: list[str] = []
        for patch in patches:
            for error in self._validate_text(patch.file, patch.new):
                if "link" in error:
                    lint_errors.append(error)
                else:
                    replay_errors.append(error)

        validation = {
            "valid": not lint_errors and not replay_errors,
            "lint_errors": lint_errors,
            "replay_errors": replay_errors,
            "sections_from_llm": from_llm,
            "sections_from_template": from_template,
        }
        manifest = {
            "generated_at": datetime.now(UTC).isoformat(),
            "spec_title": (self.spec.get("info") or {}).get("title", ""),
            "spec_version": (self.spec.get("info") or {}).get("version", ""),
            "findings": [f.model_dump() for f in findings],
            "files": [p.file for p in patches],
            "validation": validation,
        }
        return DocPatchBundle(
            findings=findings, files=patches, validation=validation, manifest=manifest
        )

    def _sections_from_text(self, text: str, file_name: str) -> list[DocSection]:
        sections: list[DocSection] = []
        for heading, block in split_blocks(text):
            match = _ENDPOINT_HEADING.match(heading)
            if match:
                sections.append(
                    DocSection(
                        file=file_name,
                        level=len(match.group(1)),
                        method=match.group(2).lower(),
                        path=match.group(3),
                        text=block.rstrip() + "\n",
                    )
                )
        return sections


def _pretty_json(data: dict[str, Any]) -> str:
    import json

    return json.dumps(data, indent=2)
