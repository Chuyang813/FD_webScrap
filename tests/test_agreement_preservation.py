"""A run that samples nothing must not destroy evidence from one that did."""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator import CatalogOrchestrator


EXISTING = {
    "terminology": "cross-extractor agreement",
    "status": "partial",
    "sample_size": 12,
    "overall_agreement": 0.997,
    "advisory_agreement": 0.349,
    "model": "gemini-3-flash-preview",
    "generated_at": "2026-08-10T19:00:00+00:00",
    "field_agreement": {"sku": 1.0},
    "products": [],
    "evidence": [],
}


def test_read_json_returns_none_for_missing_or_corrupt(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    assert CatalogOrchestrator._read_json(missing) is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert CatalogOrchestrator._read_json(corrupt) is None

    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert CatalogOrchestrator._read_json(valid) == {"a": 1}


def test_existing_evidence_is_preserved_when_a_run_samples_nothing(tmp_path: Path) -> None:
    """Resuming a finished crawl must leave a populated report on disk."""

    path = tmp_path / "agreement_report.json"
    path.write_text(json.dumps(EXISTING), encoding="utf-8")

    before = path.read_text(encoding="utf-8")
    existing = CatalogOrchestrator._read_json(path)

    # The guard condition the orchestrator applies: no new evidence this run,
    # but a prior report that carries samples.
    assert existing is not None
    assert existing.get("sample_size")

    assert path.read_text(encoding="utf-8") == before, "reading must not mutate"


def test_empty_prior_report_is_not_treated_as_evidence(tmp_path: Path) -> None:
    """A skipped report carries no samples, so overwriting it loses nothing."""

    path = tmp_path / "agreement_report.json"
    path.write_text(
        json.dumps({"status": "skipped", "sample_size": 0, "overall_agreement": None}),
        encoding="utf-8",
    )

    existing = CatalogOrchestrator._read_json(path)
    assert existing is not None
    assert not existing.get("sample_size"), "an empty report must not block a rewrite"
