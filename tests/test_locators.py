"""Locators are evaluated by parsing, never by executing page script."""

from __future__ import annotations

import pytest

from extraction.locators import (
    Locator,
    LocatorError,
    LocatorKind,
    LocatorSample,
    evaluate,
    validate_locator,
)


PAGE = """
<html><body>
  <h1 class="product-title">Nitrile Gloves</h1>
  <span class="sku-value" data-item-number="01S0630">5106359</span>
  <span class="sku-value" data-item-number="01S0631">5106360</span>
  <script>
    window.masterData = "{\\"5106359\\": {\\"sku\\": \\"5106359\\", \\"price\\": \\"9.99\\"}}";
    window.pageConfig = {"currency": "USD"};
  </script>
</body></html>
"""


def test_css_text_reads_element_content() -> None:
    locator = Locator(kind=LocatorKind.CSS_TEXT, expression="h1.product-title")
    assert evaluate(locator, PAGE) == ["Nitrile Gloves"]


def test_css_attribute_reads_named_attribute_across_matches() -> None:
    locator = Locator(
        kind=LocatorKind.CSS_ATTRIBUTE, expression=".sku-value", attribute="data-item-number"
    )
    assert evaluate(locator, PAGE) == ["01S0630", "01S0631"]


def test_js_variable_handles_double_encoded_payloads() -> None:
    locator = Locator(kind=LocatorKind.JS_VARIABLE, expression="masterData", path="*.sku")
    assert evaluate(locator, PAGE) == ["5106359"]


def test_js_variable_handles_plain_objects() -> None:
    locator = Locator(kind=LocatorKind.JS_VARIABLE, expression="pageConfig", path="currency")
    assert evaluate(locator, PAGE) == ["USD"]


def test_absent_target_yields_no_values_rather_than_raising() -> None:
    locator = Locator(kind=LocatorKind.JS_VARIABLE, expression="missingVariable")
    assert evaluate(locator, PAGE) == []


def test_malformed_locators_are_rejected_at_construction() -> None:
    with pytest.raises(LocatorError):
        Locator(kind=LocatorKind.CSS_TEXT, expression="   ")
    with pytest.raises(LocatorError):
        Locator(kind=LocatorKind.CSS_ATTRIBUTE, expression=".x")
    with pytest.raises(LocatorError):
        # A JS locator must be an identifier, never arbitrary script.
        Locator(kind=LocatorKind.JS_VARIABLE, expression="alert(1)")


def test_invalid_css_is_an_error_not_a_crash() -> None:
    locator = Locator(kind=LocatorKind.CSS_TEXT, expression="div[")
    with pytest.raises(LocatorError):
        evaluate(locator, PAGE)


def _samples() -> list[LocatorSample]:
    return [
        LocatorSample(url="a", html=PAGE, expected="5106359"),
        LocatorSample(url="b", html=PAGE.replace("5106359", "7000001"), expected="7000001"),
    ]


def test_a_candidate_passes_only_by_reproducing_every_known_value() -> None:
    locator = Locator(kind=LocatorKind.CSS_TEXT, expression=".sku-value")
    outcome = validate_locator(locator, _samples())

    assert outcome.validated is True
    assert (outcome.matched, outcome.tested) == (2, 2)


def test_partial_agreement_is_reported_not_accepted() -> None:
    """A locator right on some layouts and wrong on others is worse than none."""

    samples = _samples()
    samples[1] = LocatorSample(url="b", html=PAGE, expected="does-not-appear")
    locator = Locator(kind=LocatorKind.CSS_TEXT, expression=".sku-value")

    outcome = validate_locator(locator, samples)

    assert outcome.validated is False
    assert outcome.matched == 1 and outcome.tested == 2
    assert outcome.pass_rate == 0.5
    assert "expected 'does-not-appear'" in outcome.failures[0]


def test_an_unevaluable_candidate_reports_an_error() -> None:
    locator = Locator(kind=LocatorKind.CSS_TEXT, expression="div[")
    outcome = validate_locator(locator, _samples())

    assert outcome.validated is False
    assert outcome.error is not None
    assert "not evaluable" in outcome.summary()
