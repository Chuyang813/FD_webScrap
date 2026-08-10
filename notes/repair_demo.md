# Selector repair — live transcript

Recorded 2026-08-10 by running `python demo_repair.py` against Gemini
(`gemini-3-flash-preview`) through its OpenAI-compatible endpoint.

A layout change is simulated on a real cached product page: the brand is moved out
of the Product JSON-LD node, where the extractor reads it, and left elsewhere on
the page. The extractor then genuinely fails, which is the precondition for the
repair loop to have anything to do.

```text
1. Before the change
------------------------------------------------------------------
   brand extracted deterministically: 'Cranberry'
   source: FieldSource.JSON_LD

2. The site changes
------------------------------------------------------------------
   brand is now: None
   reported missing: ['brand']
   the value is still on the page, just not where the extractor reads it

3. Ask where it went
------------------------------------------------------------------
   provider: gemini · model: gemini-3-flash-preview
   validating against 1 page(s) whose correct value is known

4. What the model proposed, and what survived validation
------------------------------------------------------------------
   status: validated
   diagnosis: The brand information is now stored in a data attribute on the body
              element or potentially within a serialized JSON string in the window
              object.
   model confidence: 0.95

   VALIDATED  body @data-manufacturer
              reproduced 1/1 known values
              model said: The body tag contains a 'data-manufacturer' attribute
              with the value 'Cranberry', which likely represents the brand.

   rejected   window.masterData
              reproduced 0/1 known values
              model said: The window.masterData variable contains a JSON string
              with product metadata, including manufacturer details.

5. What happened to the code
------------------------------------------------------------------
   selectors modified: False
```

## Why this run is the argument for the design

The model reported **0.95 confidence** and still got one of its two proposals
wrong. `window.masterData` is a completely reasonable guess — that variable really
does hold product metadata on this page — but it does not contain the brand, and
no amount of plausibility makes it correct.

Nothing in the model's output revealed that. The validator did, by running the
candidate against a page whose correct value was already known and observing that
it reproduced 0 of 1. The good candidate was kept on the same evidence, not on the
model's say-so.

That is why confidence is recorded but never acted on, and why a proposal with no
known-good sample to test against is declined rather than reported: an unverified
suggestion is not a weaker version of a verified one, it is a different kind of
object, and mixing them would make the audit trail worthless.

## Where the ground truth comes from

The samples pair **the page as it looks now** with **the value the catalogue
already recorded**. That pairing matters: when a layout change breaks every page at
once, nothing in the current crawl can supply ground truth, but the database still
holds what the field used to be. A successful extraction from the same run is used
as a second source, which covers the partial case where only some templates
changed.

On a first run against an empty database there is no ground truth, so the advisor
declines. That is the correct behaviour, not a gap.

## Limits

- A candidate is checked for reproducing known values, not for being the most
  maintainable expression. `body @data-manufacturer` passes here; a human might
  prefer something narrower.
- Validation confirms a locator *finds* the right value. It cannot confirm the
  locator will keep doing so, or that it is not coincidentally right on the sampled
  pages.
- Adoption is deliberately manual. Nothing writes extraction code.
