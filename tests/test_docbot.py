"""DocHealer end-to-end: fixture drift -> findings -> validated patch."""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from aegisgate.llm.echo import EchoMockClient
from aegisgate.selfheal.docbot import DocHealer

FIXTURES = Path(__file__).parent / "fixtures" / "docs"


def build_healer() -> tuple[DocHealer, EchoMockClient]:
    spec = yaml.safe_load((FIXTURES / "openapi.yaml").read_text(encoding="utf-8"))
    llm = EchoMockClient()
    return DocHealer(spec, FIXTURES, llm, model="gpt-4o-mini"), llm


def test_scan_detects_all_three_drift_kinds():
    healer, _ = build_healer()
    findings, sections_by_file = healer.scan()
    kinds = {(f.kind, f.path) for f in findings}
    assert ("missing_endpoint", "/v1/widgets") in {
        (k, p) for (k, p) in kinds if k == "missing_endpoint"
    } or any(f.kind == "missing_endpoint" and f.path == "/v1/widgets" for f in findings)
    assert any(f.kind == "stale_endpoint" and f.path == "/v1/widgets/{id}" for f in findings)
    assert any(
        f.kind == "param_drift" and f.path == "/v1/widgets" and f.method == "get" for f in findings
    )
    # missing endpoints: both spec endpoints with no doc section
    missing = [f for f in findings if f.kind == "missing_endpoint"]
    assert {(f.method, f.path) for f in missing} == {
        ("post", "/v1/widgets"),
        ("get", "/v1/widgets/{id}"),
    }
    assert sections_by_file["widgets.md"]  # sections were parsed


def test_heal_end_to_end_produces_valid_patch_bundle():
    healer, llm = build_healer()
    bundle = asyncio.run(healer.heal())

    # LLM was consulted for the draft
    assert len(llm.calls) >= 1

    # one patch for the drifted file
    assert [p.file for p in bundle.files] == ["widgets.md"]
    patch = bundle.files[0]

    # the patch removes the stale section and adds the missing endpoints
    assert "-### DELETE /v1/widgets/{id}" in patch.diff
    assert "+### POST /v1/widgets" in patch.diff
    assert "+### GET /v1/widgets/{id}" in patch.diff
    # the drifted GET section is re-rendered from the spec with both params
    assert "**Params:** `limit`, `offset`" in patch.new

    # validation: lint + example replay pass against the live spec
    assert bundle.validation["valid"] is True, bundle.validation
    assert bundle.validation["lint_errors"] == []
    assert bundle.validation["replay_errors"] == []
    assert bundle.validation["sections_from_template"] >= 3

    # manifest ties it together
    assert bundle.manifest["spec_version"] == "1.3.0"
    assert len(bundle.manifest["findings"]) == len(bundle.findings)
    assert bundle.full_diff.count("@@") >= 1


def test_new_documented_params_match_spec_exactly():
    healer, _ = build_healer()
    bundle = asyncio.run(healer.heal())
    patch = bundle.files[0]
    # the *patched* text, written to a temp docs dir, must rescan clean
    tmp_docs = FIXTURES.parent / "docs_patched"
    tmp_docs.mkdir(exist_ok=True)
    (tmp_docs / "widgets.md").write_text(patch.new, encoding="utf-8")
    fresh = DocHealer(
        yaml.safe_load((FIXTURES / "openapi.yaml").read_text(encoding="utf-8")),
        tmp_docs,
        EchoMockClient(),
    )
    findings, _ = fresh.scan()
    assert findings == [], [f.model_dump() for f in findings]
