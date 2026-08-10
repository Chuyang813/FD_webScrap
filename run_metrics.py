from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic


@dataclass
class RunMetrics:
    categories_processed: int = 0
    urls_discovered: int = 0
    products_extracted: int = 0
    variants_extracted: int = 0
    http_fetches: int = 0
    browser_fetches: int = 0
    cache_hits: int = 0
    retries: int = 0
    failures: int = 0
    llm_shadow_runs: int = 0
    llm_fallback_runs: int = 0
    repair_suggestions: int = 0
    duplicates_prevented: int = 0
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    _started: float = field(default_factory=monotonic, repr=False)

    def snapshot(self) -> dict[str, object]:
        values = asdict(self)
        values.pop("_started", None)
        values["duration_seconds"] = round(monotonic() - self._started, 3)
        return values

    def write(self, path: str | Path) -> dict[str, object]:
        report = self.snapshot()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
