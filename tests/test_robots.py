from __future__ import annotations

import pytest

from fetchers import FetchRequest, FetchResult
from policy import RobotsDecision, RobotsPolicy


ROBOTS = """\
User-agent: *
Disallow: /catalog/product/view/id/
Disallow: /*?page=
Allow: /product/
"""


def test_robots_allowed_disallowed_and_unknown() -> None:
    policy = RobotsPolicy.from_text(
        ROBOTS,
        "https://www.safcodental.com/robots.txt",
        user_agent="FrontierDentalTakeHomeBot/0.1",
    )
    assert policy.check("https://www.safcodental.com/product/gloves") is RobotsDecision.ALLOWED
    assert (
        policy.check("https://www.safcodental.com/catalog/product/view/id/42")
        is RobotsDecision.DISALLOWED
    )
    assert (
        policy.check("https://www.safcodental.com/catalog/gloves?page=2")
        is RobotsDecision.DISALLOWED
    )
    assert RobotsPolicy().check("https://www.safcodental.com/product/gloves") is RobotsDecision.UNKNOWN


@pytest.mark.asyncio
async def test_robots_load_failure_remains_unknown() -> None:
    class MissingRobotsFetcher:
        async def fetch(self, request: FetchRequest) -> FetchResult:
            return FetchResult(
                url=request.url,
                status_code=503,
                body="unavailable",
                source="stub",
            )

    policy = RobotsPolicy("Bot", unknown_policy="deny")
    result = await policy.load(MissingRobotsFetcher(), "https://example.test/catalog")
    assert result is RobotsDecision.UNKNOWN
    assert not policy.is_allowed("https://example.test/product/a")

