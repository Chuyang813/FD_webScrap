"""Locators: a portable way to say "the value lives here", plus a way to test one.

A repair suggestion is only useful if it can be checked before anyone trusts it.
These locators are deliberately declarative and are evaluated by parsing, never by
executing anything, so a proposal that arrives from a language model can be run
against known-good pages safely.

The validation harness is the point. The database already holds the correct value
for every page that was extracted successfully, so those pages form a ready-made
regression suite: a candidate locator earns adoption only by reproducing values
that are already known to be right.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from bs4 import BeautifulSoup


class LocatorKind(str, Enum):
    #: Text content of the elements a CSS selector matches.
    CSS_TEXT = "css_text"
    #: A named attribute of the elements a CSS selector matches.
    CSS_ATTRIBUTE = "css_attribute"
    #: A ``window.<name> = …`` assignment, JSON-decoded (twice if double-encoded).
    JS_VARIABLE = "js_variable"


class LocatorError(ValueError):
    """The locator is malformed and cannot be evaluated."""


_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


@dataclass(frozen=True, slots=True)
class Locator:
    kind: LocatorKind
    expression: str
    attribute: str | None = None
    #: Dotted path applied to a decoded JS value, e.g. "0.sku" or "items.sku".
    path: str | None = None

    def describe(self) -> str:
        if self.kind is LocatorKind.CSS_ATTRIBUTE:
            return f"{self.expression} @{self.attribute}"
        if self.kind is LocatorKind.JS_VARIABLE:
            return f"window.{self.expression}" + (f" → {self.path}" if self.path else "")
        return self.expression

    def __post_init__(self) -> None:
        if not self.expression or not self.expression.strip():
            raise LocatorError("locator expression cannot be blank")
        if self.kind is LocatorKind.CSS_ATTRIBUTE and not self.attribute:
            raise LocatorError("css_attribute locators require an attribute name")
        if self.kind is LocatorKind.JS_VARIABLE and not _IDENTIFIER.match(self.expression):
            raise LocatorError(f"not a JavaScript identifier: {self.expression!r}")


def _decode_js_value(html: str, name: str) -> Any:
    """Read ``window.<name> = …``, tolerating Safco's double-encoded payloads."""

    pattern = re.compile(rf"\bwindow\.{re.escape(name)}\s*=", re.ASCII)
    match = pattern.search(html)
    if not match:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(html[match.end() :].lstrip())
    except json.JSONDecodeError:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _walk(value: Any, path: str) -> list[Any]:
    """Follow a dotted path, fanning out across every element of a collection."""

    current: list[Any] = [value]
    for step in (part for part in path.split(".") if part):
        nxt: list[Any] = []
        for item in current:
            if isinstance(item, dict):
                if step == "*":
                    nxt.extend(item.values())
                elif step in item:
                    nxt.append(item[step])
            elif isinstance(item, list):
                if step == "*":
                    nxt.extend(item)
                elif step.isdigit() and int(step) < len(item):
                    nxt.append(item[int(step)])
        current = nxt
    return current


def _stringify(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def evaluate(locator: Locator, html: str) -> list[str]:
    """Return every value the locator finds, in document order, deduplicated.

    Evaluation never executes page script; CSS is matched by the parser and JS
    values are read as JSON.
    """

    found: list[str] = []

    def keep(value: str | None) -> None:
        if value and value not in found:
            found.append(value)

    if locator.kind is LocatorKind.JS_VARIABLE:
        decoded = _decode_js_value(html, locator.expression)
        if decoded is None:
            return []
        targets = _walk(decoded, locator.path) if locator.path else [decoded]
        for target in targets:
            keep(_stringify(target))
        return found

    soup = BeautifulSoup(html, "html.parser")
    try:
        elements = soup.select(locator.expression)
    except Exception as exc:  # An invalid selector is a bad proposal, not a crash.
        raise LocatorError(f"invalid CSS selector {locator.expression!r}: {exc}") from exc

    for element in elements:
        if locator.kind is LocatorKind.CSS_ATTRIBUTE:
            raw = element.get(locator.attribute or "")
            if isinstance(raw, list):
                raw = " ".join(raw)
            keep(_stringify(raw))
        else:
            keep(_stringify(element.get_text(" ", strip=True)))
    return found


@dataclass(frozen=True, slots=True)
class LocatorSample:
    """A page whose correct value for the field is already known."""

    url: str
    html: str
    expected: str


@dataclass(slots=True)
class ValidationOutcome:
    locator: Locator
    tested: int = 0
    matched: int = 0
    failures: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def pass_rate(self) -> float:
        return self.matched / self.tested if self.tested else 0.0

    @property
    def validated(self) -> bool:
        return self.error is None and self.tested > 0 and self.matched == self.tested

    def summary(self) -> str:
        if self.error:
            return f"not evaluable: {self.error}"
        return f"reproduced {self.matched}/{self.tested} known values"


def validate_locator(locator: Locator, samples: list[LocatorSample]) -> ValidationOutcome:
    """Test a candidate against pages whose correct value is already established.

    A candidate passes only by reproducing *every* known value. Partial agreement
    is reported rather than accepted: a locator that works on some layouts and
    silently returns the wrong string on others is worse than no locator.
    """

    outcome = ValidationOutcome(locator=locator)
    for sample in samples:
        try:
            values = evaluate(locator, sample.html)
        except LocatorError as exc:
            outcome.error = str(exc)
            outcome.failures.clear()
            outcome.tested = 0
            outcome.matched = 0
            return outcome
        outcome.tested += 1
        expected = sample.expected.strip()
        if any(value.strip() == expected for value in values):
            outcome.matched += 1
        else:
            preview = ", ".join(values[:3]) if values else "nothing"
            outcome.failures.append(f"{sample.url}: expected {expected!r}, found {preview}")
    return outcome
