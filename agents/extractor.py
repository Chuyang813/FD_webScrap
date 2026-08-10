"""Agent boundary around the deterministic product extractor."""

from __future__ import annotations

from extraction import DeterministicProductExtractor
from models import ExtractionResult


class ExtractorAgent:
    def __init__(self, extractor: DeterministicProductExtractor | None = None) -> None:
        self._extractor = extractor or DeterministicProductExtractor()

    def extract(self, html: str, url: str) -> ExtractionResult:
        return self._extractor.extract(html, url)


ProductExtractorAgent = ExtractorAgent
