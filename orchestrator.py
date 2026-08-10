from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agents import (
    DiscoveredURL,
    ExtractorAgent,
    Navigator,
    PageClassifier,
    RecoveryAgent,
    ValidatorAgent,
)
from app_config import AppConfig, CategoryConfig
from extraction.comparator import ExtractionComparator
from extraction.field_values import REPAIRABLE_FIELDS, known_value, repairable_field
from extraction.locators import LocatorSample
from fetchers import CachedFetcher, FetchRequest, HttpFetcher
from llm import LLMSettings, ShadowEvidence, ShadowLLMExtractor
from llm.repair import SelectorRepairAdvisor
from models import (
    CrawlStatus,
    ExtractionResult,
    FieldSource,
    PageType,
    PriceVisibility,
    Product,
)
from policy import RobotsDecision, RobotsPolicy
from reporting import write_dashboard
from run_metrics import RunMetrics
from storage import Database, ProductRepository, export_products_csv, export_products_json
from utils.logging import get_json_logger, log_event
from utils.progress import ProgressReporter


@dataclass(slots=True)
class RunOptions:
    max_products: int | None = None
    resume: bool = False
    force_refresh: bool = False
    offline: bool = False
    no_llm: bool = False
    shadow_sample: int | None = None
    progress: bool | None = None
    log_path: str | None = None


@dataclass(slots=True)
class CrawlCandidate:
    category: CategoryConfig
    discovered: DiscoveredURL


class CatalogOrchestrator:
    """Small, explicit coordinator; agents keep their own narrow responsibilities."""

    def __init__(self, config: AppConfig, options: RunOptions | None = None) -> None:
        self.config = config
        self.options = options or RunOptions()
        self.progress = ProgressReporter(enabled=self.options.progress)
        # A live progress view owns stderr, so the JSON stream moves to a file.
        log_path = self.options.log_path
        if log_path is None and self.progress.enabled:
            log_path = "logs/crawler.jsonl"
        self.log_path = log_path
        self.logger = get_json_logger(log_path=log_path)
        self.metrics = RunMetrics()
        self.database = Database(config.storage.sqlite_path)
        self.repository = ProductRepository(self.database)

        self.http_fetcher = HttpFetcher.from_config(config)
        self.fetcher = CachedFetcher.from_config(
            self.http_fetcher,
            config,
            offline=self.options.offline,
        )
        self.robots = RobotsPolicy(
            config.fetch.user_agent,
            unknown_policy=config.robots.unknown_policy,
        )
        enforced_policy = self.robots if config.robots.enforce else None
        self.navigator = Navigator(self.fetcher, robots_policy=enforced_policy)
        self.classifier = PageClassifier()
        self.extractor = ExtractorAgent()
        self.validator = ValidatorAgent()
        self.recovery = RecoveryAgent(
            agreement_threshold=config.llm.agreement_threshold
        )
        self.comparator = ExtractionComparator()

        self.llm_settings = LLMSettings.from_env()
        self.shadow_extractor = ShadowLLMExtractor(
            self.llm_settings,
            requests_per_minute=config.llm.requests_per_minute,
            max_retries=config.llm.max_retries,
        )
        self.repair_advisor = SelectorRepairAdvisor(self.llm_settings)
        # Successful pages double as the regression suite a repair candidate must
        # satisfy, so they are kept from the run itself rather than re-fetched.
        self.repair_samples: list[tuple[str, str, dict[str, str]]] = []
        self.repair_targets: dict[str, str] = {}
        self.repair_diagnoses: list[dict[str, Any]] = []
        self.shadow_pairs: list[tuple[ExtractionResult, ExtractionResult]] = []
        self.shadow_evidence: list[dict[str, Any]] = []
        self.recovery_records: list[dict[str, Any]] = []
        self.extraction_audits: list[dict[str, Any]] = []
        self.discovery: dict[str, dict[str, Any]] = {}
        self.robots_decision = RobotsDecision.UNKNOWN
        self.products_planned = 0

    async def run(self) -> dict[str, Any]:
        try:
            self.progress.run_started(
                categories=[category.name for category in self.config.categories],
                mode=self._describe_mode(),
            )
            await self._load_robots()
            discovered = await self._discover_categories()
            candidates = self._eligible_candidates(discovered)
            self.products_planned = len(candidates)
            self.progress.crawl_started(
                total=len(candidates),
                note=self._nothing_pending_note() if not candidates else None,
            )
            for candidate in candidates:
                await self._process_product(candidate)
            await self._run_repair_diagnosis()
            export_products_json(self.repository, self.config.output.json_path)
            export_products_csv(self.repository, self.config.output.csv_path)
            agreement = self._write_agreement_report()
            report = self._write_run_report(agreement)
            self._write_dashboard()
            self.progress.shadow(
                status=str(agreement.get("status", "skipped")),
                sample=int(agreement.get("sample_size") or 0),
                agreement=agreement.get("overall_agreement"),
            )
            self.progress.finished(
                status=str(report.get("status", "failed")),
                outputs=self._progress_outputs(),
                stored=(
                    self.repository.count_products(),
                    self.repository.count_variants(),
                ),
            )
            log_event(
                self.logger,
                "export_complete",
                json_path=str(self.config.output.json_path),
                csv_path=str(self.config.output.csv_path),
                products=self.repository.count_products(),
                variants=self.repository.count_variants(),
            )
            return report
        finally:
            self.metrics.http_fetches = self.http_fetcher.request_count
            self.metrics.retries = self.http_fetcher.retry_count
            self.metrics.cache_hits = self.fetcher.stats.hits
            await self.http_fetcher.aclose()
            self.database.close()

    def _describe_mode(self) -> str:
        flags = []
        if self.options.offline:
            flags.append("offline")
        if self.options.force_refresh:
            flags.append("force-refresh")
        if self.options.resume:
            flags.append("resume")
        flags.append("no-llm" if self.options.no_llm else "llm-shadow")
        return ", ".join(flags)

    def _remember_repair_sample(
        self,
        url: str,
        html: str,
        *,
        result: ExtractionResult | None = None,
        previous: Product | None = None,
    ) -> None:
        """Pair a page as it looks now with values already known to be correct.

        Two sources, because they cover different failures. A stored record from an
        earlier run is the stronger one: when a layout change breaks every page at
        once, nothing in the current run can supply ground truth, but the database
        still holds what the field used to be. A successful extraction from this
        run covers the partial case, where only some templates changed.
        """

        if len(self.repair_samples) >= self.config.llm.repair_samples:
            return
        values: dict[str, str] = {}
        if previous is not None:
            for name in ("name", "brand", "description"):
                value = getattr(previous, name, None)
                if value:
                    values[name] = str(value).strip()
        if result is not None:
            for name in REPAIRABLE_FIELDS:
                value = known_value(result, name)
                if value:
                    values.setdefault(name, value)
        if values:
            self.repair_samples.append((url, html, values))

    def _note_repair_targets(self, missing_fields: list[str], html: str) -> None:
        """Record the first failing page per field; later ones add no information."""

        for reported in missing_fields:
            field_name = repairable_field(reported)
            if field_name and field_name not in self.repair_targets:
                self.repair_targets[field_name] = html

    async def _run_repair_diagnosis(self) -> None:
        """Ask for candidate locators, then keep only those that prove themselves."""

        if (
            self.options.no_llm
            or not self.config.llm.enabled
            or not self.config.llm.repair_enabled
            or not self.repair_targets
            or self.config.llm.repair_max_fields <= 0
        ):
            return

        for field_name in list(self.repair_targets)[: self.config.llm.repair_max_fields]:
            samples = [
                LocatorSample(url=url, html=html, expected=values[field_name])
                for url, html, values in self.repair_samples
                if field_name in values
            ]
            diagnosis = await self.repair_advisor.diagnose(
                field_name=field_name,
                failing_html=self.repair_targets[field_name],
                samples=samples,
            )
            record = diagnosis.model_dump(mode="json")
            self.repair_diagnoses.append(record)
            if diagnosis.request_sent:
                self.metrics.repair_suggestions += len(diagnosis.accepted)
            log_event(
                self.logger,
                "repair_diagnosis",
                field=field_name,
                status=diagnosis.status,
                samples=len(samples),
                validated=len(diagnosis.accepted),
                selectors_modified=False,
            )

    def _write_dashboard(self) -> None:
        """Presentation must never be able to fail a crawl that already succeeded."""

        try:
            write_dashboard(
                self.config.output.dashboard_path,
                sqlite_path=self.config.storage.sqlite_path,
                run_report_path=self.config.output.run_report_path,
                agreement_path=self.config.output.agreement_path,
                agreement_threshold=self.config.llm.agreement_threshold,
            )
        except Exception as exc:
            log_event(
                self.logger,
                "dashboard_render_failed",
                path=str(self.config.output.dashboard_path),
                error=f"{type(exc).__name__}: {exc}",
            )

    def _nothing_pending_note(self) -> str:
        """Explain an empty work list so it does not read as a failed crawl."""

        discovered = self.metrics.urls_discovered
        if self.options.resume and discovered:
            return (
                f"all {discovered} discovered families are already complete; "
                "use --force-refresh to re-extract them"
            )
        if not discovered:
            return "discovery returned no product URLs"
        if self.options.max_products == 0:
            return "--max-products is zero"
        return "no eligible product URLs remained after filtering"

    def _progress_outputs(self) -> dict[str, str]:
        outputs = {
            "sqlite": str(self.config.storage.sqlite_path),
            "json": str(self.config.output.json_path),
            "csv": str(self.config.output.csv_path),
            "report": str(self.config.output.run_report_path),
        }
        if self.log_path:
            outputs["log"] = str(self.log_path)
        return outputs

    async def _load_robots(self) -> None:
        category = self.config.categories[0]
        parsed = urlsplit(category.url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            response = await self.fetcher.fetch(
                FetchRequest(
                    url=robots_url,
                    force_refresh=self.options.force_refresh,
                )
            )
            if 200 <= response.status_code < 300:
                self.robots.load_text(response.body, robots_url)
                self.robots_decision = self.robots.check(category.url)
            else:
                self.robots_decision = RobotsDecision.UNKNOWN
        except Exception as exc:
            self.robots_decision = RobotsDecision.UNKNOWN
            log_event(
                self.logger,
                "robots_fetch_failed",
                url=robots_url,
                error=f"{type(exc).__name__}: {exc}",
            )

        log_event(
            self.logger,
            "robots_policy_checked",
            url=robots_url,
            decision=self.robots_decision.value,
            enforced=self.config.robots.enforce,
        )
        self.progress.robots(
            decision=self.robots_decision.value.upper(),
            enforced=self.config.robots.enforce,
        )
        if (
            self.config.robots.enforce
            and self.robots_decision is RobotsDecision.UNKNOWN
            and self.config.robots.unknown_policy == "deny"
        ):
            raise RuntimeError("robots policy is unavailable; conservative policy denies crawling")

    async def _discover_categories(self) -> list[list[CrawlCandidate]]:
        groups: list[list[CrawlCandidate]] = []
        for category in self.config.categories:
            self.repository.enqueue_url(category.url, PageType.CATEGORY)
            decision = self.robots.check(category.url)
            if self.config.robots.enforce and decision is not RobotsDecision.ALLOWED:
                self.repository.mark_url_skipped(
                    category.url,
                    f"robots decision: {decision.value}",
                )
                self.discovery[category.name] = {
                    "url": category.url,
                    "status": "skipped",
                    "robots": decision.value,
                    "count": 0,
                }
                groups.append([])
                continue

            self.repository.mark_url_in_progress(category.url, PageType.CATEGORY)
            log_event(self.logger, "category_discovery_started", url=category.url, category=category.name)
            try:
                records = await self.navigator.discover(
                    category.url,
                    force_refresh=self.options.force_refresh,
                )
                if not records:
                    raise ValueError("category discovery returned no product URLs")
                self.repository.mark_url_complete(category.url, page_type=PageType.CATEGORY)
                self.metrics.categories_processed += 1
                degraded = self.navigator.last_method != "algolia"
                self.discovery[category.name] = {
                    "url": category.url,
                    "status": "degraded" if degraded else "completed",
                    "method": self.navigator.last_method,
                    "count": len(records),
                    "reported_total_hits": self.navigator.last_total_hits,
                    "degraded_reason": (
                        "Algolia discovery failed; using first-page JSON-LD fallback"
                        if degraded
                        else None
                    ),
                }
                groups.append(
                    [CrawlCandidate(category=category, discovered=record) for record in records]
                )
                log_event(
                    self.logger,
                    "category_discovery_completed",
                    url=category.url,
                    category=category.name,
                    method=self.navigator.last_method,
                    discovered=len(records),
                )
                self.progress.category_discovered(
                    name=category.name,
                    count=len(records),
                    method=str(self.navigator.last_method),
                    degraded=degraded,
                )
            except Exception as exc:
                self.metrics.failures += 1
                error = f"{type(exc).__name__}: {exc}"
                self.repository.mark_url_failed(category.url, error)
                self.repository.record_error(
                    category.url,
                    type(exc).__name__,
                    str(exc),
                    context={"page_type": "category", "category": category.name},
                )
                self.discovery[category.name] = {
                    "url": category.url,
                    "status": "failed",
                    "error": error,
                    "count": 0,
                }
                groups.append([])
                log_event(
                    self.logger,
                    "category_discovery_failed",
                    url=category.url,
                    category=category.name,
                    error=error,
                )
                self.progress.category_failed(name=category.name, error=error)

        unique_groups = self._deduplicate_round_robin(groups)
        self.metrics.urls_discovered = sum(len(group) for group in unique_groups)
        for group in unique_groups:
            for candidate in group:
                self.repository.enqueue_url(candidate.discovered.url, PageType.PRODUCT)
        return unique_groups

    @staticmethod
    def _deduplicate_round_robin(
        groups: list[list[CrawlCandidate]],
    ) -> list[list[CrawlCandidate]]:
        seen: set[str] = set()
        result: list[list[CrawlCandidate]] = [[] for _ in groups]
        for index, group in enumerate(groups):
            for candidate in group:
                if candidate.discovered.url in seen:
                    continue
                seen.add(candidate.discovered.url)
                result[index].append(candidate)
        return result

    def _eligible_candidates(
        self,
        groups: list[list[CrawlCandidate]],
    ) -> list[CrawlCandidate]:
        interleaved: list[CrawlCandidate] = []
        positions = [0] * len(groups)
        while True:
            added = False
            for index, group in enumerate(groups):
                while positions[index] < len(group):
                    candidate = group[positions[index]]
                    positions[index] += 1
                    if self._eligible(candidate.discovered.url):
                        interleaved.append(candidate)
                        added = True
                        break
            if not added:
                break

        limit = (
            self.options.max_products
            if self.options.max_products is not None
            else self.config.crawler.max_products
        )
        return interleaved if limit is None else interleaved[:limit]

    def _eligible(self, url: str) -> bool:
        if not self.options.resume:
            return True
        state = self.repository.get_crawl_state(url)
        if state is None:
            return True
        if state.status in {CrawlStatus.COMPLETED, CrawlStatus.SKIPPED}:
            return False
        if state.status is CrawlStatus.FAILED:
            return state.attempt_count <= self.config.crawler.max_retries
        return True

    async def _process_product(self, candidate: CrawlCandidate) -> None:
        url = candidate.discovered.url
        if self.config.robots.enforce and self.robots.check(url) is not RobotsDecision.ALLOWED:
            self.repository.mark_url_skipped(url, "robots disallowed")
            log_event(self.logger, "url_skipped", url=url, reason="robots")
            return

        state = self.repository.mark_url_in_progress(url, PageType.PRODUCT)
        started = datetime.now(UTC)
        try:
            fetched = await self.fetcher.fetch(
                FetchRequest(url=url, force_refresh=self.options.force_refresh)
            )
            if not 200 <= fetched.status_code < 300:
                raise RuntimeError(f"product fetch returned HTTP {fetched.status_code}")
            self._require_allowed_final_url(url, fetched.url)
            classification = self.classifier.classify(fetched)
            if classification.page_type is not PageType.PRODUCT:
                raise ValueError(
                    f"expected product page, classified {classification.page_type.value}"
                )
            raw = self.extractor.extract(fetched.body, fetched.url)
            validation = self.validator.validate(raw)
            if not validation.valid or validation.result is None:
                recovery = self.recovery.assess(raw, validation=validation)
                self.recovery_records.append(recovery.model_dump(mode="json"))
                raise ValueError("validation failed: " + "; ".join(validation.errors))
            result = validation.result

            previous_product = self.repository.get_product(result.product.product_url)
            missing_regressions = [
                field
                for field in ("brand", "category_path", "description", "image_urls")
                if previous_product is not None
                and getattr(previous_product, field) not in (None, "", [], {})
                and getattr(result.product, field) in (None, "", [], {})
            ]
            if missing_regressions:
                result.variants_complete = False
                result.warnings.append(
                    "previously populated product fields became empty: "
                    + ", ".join(missing_regressions)
                )
                result.missing_expected_fields.extend(
                    f"metadata_drift:{field}" for field in missing_regressions
                )

            existing_variants = self.repository.list_variants(result.product.product_url)
            if (
                result.variants_complete
                and len(existing_variants) >= 4
                and len(result.variants) < len(existing_variants) * 0.5
            ):
                warning = (
                    f"variant count dropped from {len(existing_variants)} to "
                    f"{len(result.variants)}; preserving prior variant snapshot"
                )
                result.variants_complete = False
                result.warnings.append(warning)
                result.missing_expected_fields.append("variant_count_drift")
                result.method_summary["variants"] = "preserved_after_count_drift"

            existed = previous_product is not None
            if not result.variants_complete:
                if not existed:
                    self.repository.upsert_extraction(result)
                recovery = self.recovery.assess(result, validation=validation)
                self.recovery_records.append(recovery.model_dump(mode="json"))
                self.extraction_audits.append(
                    {
                        "product_url": result.product.product_url,
                        "variants_extracted": len(result.variants),
                        "variants_complete": False,
                        "warnings": result.warnings,
                        "missing_expected_fields": result.missing_expected_fields,
                        "method_summary": result.method_summary,
                    }
                )
                self._note_repair_targets(result.missing_expected_fields, fetched.body)
                # The changed page is the one a candidate has to work on, and the
                # stored record supplies the value it must reproduce.
                self._remember_repair_sample(
                    fetched.url, fetched.body, result=result, previous=previous_product
                )
                raise ValueError(
                    "incomplete variant snapshot was retained for review and retry"
                )
            self.repository.upsert_extraction(result)
            if existed:
                self.metrics.duplicates_prevented += 1
            self.repository.mark_url_complete(
                url,
                page_type=PageType.PRODUCT,
                content_hash=result.product.content_hash,
                fetched_at=fetched.fetched_at,
            )
            self.metrics.products_extracted += 1
            self.metrics.variants_extracted += len(result.variants)
            self.extraction_audits.append(
                {
                    "product_url": result.product.product_url,
                    "variants_extracted": len(result.variants),
                    "variants_complete": result.variants_complete,
                    "warnings": result.warnings,
                    "missing_expected_fields": result.missing_expected_fields,
                    "method_summary": result.method_summary,
                }
            )

            self._remember_repair_sample(
                fetched.url, fetched.body, result=result, previous=previous_product
            )
            recovery = self.recovery.assess(result, validation=validation)
            if recovery.disposition != "no_action":
                self.recovery_records.append(recovery.model_dump(mode="json"))
            await self._maybe_shadow(result, fetched.body, fetched.url)
            log_event(
                self.logger,
                "extraction_success",
                url=result.product.product_url,
                category=candidate.category.name,
                fetch_source=fetched.source,
                from_cache=fetched.from_cache,
                attempt=state.attempt_count,
                duration_ms=round((datetime.now(UTC) - started).total_seconds() * 1000),
                extraction_method=result.method_summary,
                variants=len(result.variants),
            )
            self.progress.product_done(
                category=candidate.category.name,
                url=result.product.product_url,
                variants=len(result.variants),
                from_cache=fetched.from_cache,
                ok=True,
            )
        except Exception as exc:
            self.metrics.failures += 1
            error = f"{type(exc).__name__}: {exc}"
            self.repository.mark_url_failed(url, error)
            self.repository.record_error(
                url,
                type(exc).__name__,
                str(exc),
                context={
                    "category": candidate.category.name,
                    "page_type": "product",
                    "discovery_method": candidate.discovered.method,
                },
            )
            log_event(
                self.logger,
                "extraction_failed",
                url=url,
                category=candidate.category.name,
                attempt=state.attempt_count,
                error=error,
            )
            self.progress.product_done(
                category=candidate.category.name,
                url=url,
                ok=False,
                error=error,
            )

    async def _maybe_shadow(
        self,
        deterministic: ExtractionResult,
        html: str,
        url: str,
    ) -> None:
        sample_limit = (
            self.options.shadow_sample
            if self.options.shadow_sample is not None
            else self.config.llm.shadow_sample
        )
        if (
            self.options.no_llm
            or not self.config.llm.enabled
            or sample_limit <= 0
            or len(self.shadow_evidence) >= sample_limit
        ):
            return
        evidence = await self.shadow_extractor.extract(html, url)
        record = evidence.model_dump(mode="json", exclude={"result"})
        record["product_url"] = deterministic.product.product_url
        self.shadow_evidence.append(record)
        if evidence.request_sent:
            self.metrics.llm_shadow_runs += 1
        if evidence.status == "completed" and evidence.result is not None:
            self.shadow_pairs.append((deterministic, evidence.result))
            agreement = self.comparator.compare(deterministic, evidence.result)
            recovery = self.recovery.assess(deterministic, agreement=agreement)
            if recovery.disposition != "no_action":
                self.recovery_records.append(recovery.model_dump(mode="json"))

    def _write_agreement_report(self) -> dict[str, Any]:
        sample_limit = (
            self.options.shadow_sample
            if self.options.shadow_sample is not None
            else self.config.llm.shadow_sample
        )
        if self.options.no_llm:
            status, reason = "skipped", "disabled by --no-llm"
        elif not self.config.llm.enabled:
            status, reason = "skipped", "disabled by config"
        elif not self.llm_settings.configured:
            status = "skipped"
            reason = "missing configuration: " + ", ".join(
                self.llm_settings.missing_configuration
            )
        elif not self.shadow_evidence:
            status, reason = "skipped", "no eligible product samples"
        elif self.shadow_pairs and len(self.shadow_pairs) == len(self.shadow_evidence):
            status, reason = "completed", None
        elif self.shadow_pairs:
            status, reason = "partial", "one or more shadow samples failed"
        else:
            status, reason = "failed", "all shadow samples failed"

        summary = self.comparator.summarize(self.shadow_pairs)
        # A run that sampled nothing must not erase evidence produced by one that
        # did. Resuming a finished crawl would otherwise silently destroy the
        # report simply because there was no remaining work.
        if not self.shadow_evidence:
            existing = self._read_json(self.config.output.agreement_path)
            if existing and existing.get("sample_size"):
                log_event(
                    self.logger,
                    "agreement_report_preserved",
                    path=str(self.config.output.agreement_path),
                    preserved_sample_size=existing.get("sample_size"),
                    reason=reason,
                )
                return {
                    "status": "preserved",
                    "reason": (
                        f"this run produced no shadow samples ({reason}); "
                        "the existing report was kept"
                    ),
                    "sample_size": 0,
                    "overall_agreement": None,
                    "preserved_report": {
                        "sample_size": existing.get("sample_size"),
                        "overall_agreement": existing.get("overall_agreement"),
                        "advisory_agreement": existing.get("advisory_agreement"),
                        "generated_at": existing.get("generated_at"),
                        "model": existing.get("model"),
                    },
                }

        report = {
            "terminology": "cross-extractor agreement",
            "status": status,
            "reason": reason,
            "generated_at": datetime.now(UTC).isoformat(),
            "provider": self.llm_settings.provider,
            "model": self.llm_settings.model,
            "sample_requested": sample_limit,
            "sample_size": summary.sample_size,
            # The headline value covers core fields only; advisory fields are
            # reported separately because they disagree for benign reasons.
            "overall_agreement": summary.overall_agreement if summary.sample_size else None,
            "advisory_agreement": summary.advisory_agreement if summary.sample_size else None,
            "core_fields": summary.core_fields,
            "advisory_fields": summary.advisory_fields,
            "field_agreement": summary.field_agreement,
            "products": [item.model_dump(mode="json") for item in summary.products],
            "evidence": self.shadow_evidence,
        }
        self._write_json(self.config.output.agreement_path, report)
        return report

    def _write_run_report(self, agreement: dict[str, Any]) -> dict[str, Any]:
        self.metrics.http_fetches = self.http_fetcher.request_count
        self.metrics.retries = self.http_fetcher.retry_count
        self.metrics.cache_hits = self.fetcher.stats.hits
        snapshot = self.repository.list_extractions()
        degraded_discovery = any(
            item.get("status") == "degraded" for item in self.discovery.values()
        )
        if self.metrics.failures:
            status = "partial" if self.metrics.products_extracted else "failed"
        elif degraded_discovery:
            status = "partial"
        elif not self.options.resume and self.products_planned == 0:
            status = "failed"
        else:
            status = "completed"
        report = {
            "status": status,
            "metrics": self.metrics.snapshot(),
            "robots": {
                "decision": self.robots_decision.value,
                "enforced": self.config.robots.enforce,
                "unknown_policy": self.config.robots.unknown_policy,
            },
            "discovery": self.discovery,
            "products_planned": self.products_planned,
            "database_snapshot": self._quality_summary(snapshot),
            "agreement": {
                key: agreement[key]
                for key in (
                    "status",
                    "reason",
                    "sample_size",
                    "overall_agreement",
                    "advisory_agreement",
                    # Present when this run sampled nothing and an earlier report
                    # was kept, so "preserved" says what it preserved.
                    "preserved_report",
                )
                if key in agreement
            },
            "recovery_records": self.recovery_records,
            "repair_diagnoses": self.repair_diagnoses,
            "extraction_audits": self.extraction_audits,
            "outputs": {
                "sqlite": str(self.config.storage.sqlite_path),
                "json": str(self.config.output.json_path),
                "csv": str(self.config.output.csv_path),
                "agreement": str(self.config.output.agreement_path),
                "run_report": str(self.config.output.run_report_path),
            },
        }
        self._write_json(self.config.output.run_report_path, report)
        return report

    @staticmethod
    def _quality_summary(results: list[ExtractionResult]) -> dict[str, Any]:
        products = [item.product for item in results]
        variants = [variant for item in results for variant in item.variants]

        def completeness(items: list[Any], fields: list[str]) -> dict[str, float]:
            if not items:
                return {field: 0.0 for field in fields}
            return {
                field: round(
                    sum(
                        getattr(item, field) not in (None, "", [], {}, PriceVisibility.UNKNOWN)
                        for item in items
                    )
                    / len(items),
                    4,
                )
                for field in fields
            }

        provenance: dict[str, int] = {}
        for item in [*products, *variants]:
            for source in item.field_provenance.values():
                key = source.value if isinstance(source, FieldSource) else str(source)
                provenance[key] = provenance.get(key, 0) + 1

        category_counts: dict[str, int] = {}
        for product in products:
            key = " > ".join(product.category_path) or "unknown"
            category_counts[key] = category_counts.get(key, 0) + 1

        return {
            "products": len(products),
            "variants": len(variants),
            "product_completeness": completeness(
                products,
                [
                    "name",
                    "brand",
                    "category_path",
                    "description",
                    "specifications",
                    "image_urls",
                    "alternative_product_urls",
                    "price_visibility",
                ],
            ),
            "variant_completeness": completeness(
                variants,
                [
                    "sku",
                    "item_number",
                    "price",
                    "unit_pack_size",
                    "availability",
                    "price_visibility",
                ],
            ),
            "category_path_counts": category_counts,
            "field_provenance_counts": dict(sorted(provenance.items())),
        }

    @staticmethod
    def _write_json(path: str | Path, value: Any) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def _read_json(path: str | Path) -> dict[str, Any] | None:
        """Read a previous artifact; an unreadable one is treated as absent."""

        target = Path(path)
        if not target.exists():
            return None
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def _require_allowed_final_url(self, requested_url: str, final_url: str) -> None:
        requested = urlsplit(requested_url)
        final = urlsplit(final_url)
        if requested.netloc.casefold() != final.netloc.casefold():
            raise ValueError(f"cross-origin redirect refused: {final_url}")
        if self.config.robots.enforce and self.robots.check(final_url) is not RobotsDecision.ALLOWED:
            raise ValueError(f"redirect target is not allowed by robots policy: {final_url}")
