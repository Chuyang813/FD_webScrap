"""Robots.txt loading and deterministic crawl decisions."""

from __future__ import annotations

import re
from enum import Enum
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from fetchers import FetchRequest, Fetcher


class RobotsDecision(str, Enum):
    ALLOWED = "ALLOWED"
    DISALLOWED = "DISALLOWED"
    UNKNOWN = "UNKNOWN"


class RobotsPolicyError(RuntimeError):
    pass


class RobotsDisallowedError(RobotsPolicyError):
    pass


class RobotsPolicy:
    """Parsed robots policy whose unavailable state remains explicit."""

    def __init__(self, user_agent: str = "*", *, unknown_policy: str = "deny") -> None:
        if unknown_policy not in {"allow", "deny"}:
            raise ValueError("unknown_policy must be 'allow' or 'deny'")
        self.user_agent = user_agent
        self.unknown_policy = unknown_policy
        self.robots_url: str | None = None
        self._parser: RobotFileParser | None = None
        self._rules: list[tuple[bool, str]] = []

    @classmethod
    def from_text(
        cls,
        text: str,
        robots_url: str,
        *,
        user_agent: str = "*",
        unknown_policy: str = "deny",
    ) -> "RobotsPolicy":
        policy = cls(user_agent, unknown_policy=unknown_policy)
        policy.load_text(text, robots_url)
        return policy

    async def load(self, fetcher: Fetcher, site_url: str) -> RobotsDecision:
        parsed = urlsplit(site_url)
        self.robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
        try:
            result = await fetcher.fetch(FetchRequest(url=self.robots_url))
        except Exception:
            self._parser = None
            return RobotsDecision.UNKNOWN
        if not 200 <= result.status_code < 300:
            self._parser = None
            return RobotsDecision.UNKNOWN
        self.load_text(result.body, self.robots_url)
        return RobotsDecision.ALLOWED

    def load_text(self, text: str, robots_url: str) -> None:
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(text.splitlines())
        self.robots_url = robots_url
        self._parser = parser
        self._rules = _applicable_rules(text, self.user_agent)

    def check(self, url: str) -> RobotsDecision:
        if self._parser is None:
            return RobotsDecision.UNKNOWN
        explicit = _rule_decision(self._rules, url)
        if explicit is not None:
            return explicit
        return (
            RobotsDecision.ALLOWED
            if self._parser.can_fetch(self.user_agent, url)
            else RobotsDecision.DISALLOWED
        )

    def is_allowed(self, url: str) -> bool:
        decision = self.check(url)
        if decision is RobotsDecision.UNKNOWN:
            return self.unknown_policy == "allow"
        return decision is RobotsDecision.ALLOWED

    def require_allowed(self, url: str) -> None:
        decision = self.check(url)
        if decision is RobotsDecision.DISALLOWED:
            raise RobotsDisallowedError(f"robots.txt disallows {url}")
        if decision is RobotsDecision.UNKNOWN and self.unknown_policy == "deny":
            raise RobotsDisallowedError(f"robots.txt policy is unknown for {url}")


def evaluate_robots(
    robots_text: str,
    url: str,
    *,
    robots_url: str = "https://example.invalid/robots.txt",
    user_agent: str = "*",
) -> RobotsDecision:
    return RobotsPolicy.from_text(
        robots_text,
        robots_url,
        user_agent=user_agent,
    ).check(url)


def _applicable_rules(text: str, user_agent: str) -> list[tuple[bool, str]]:
    """Select one robots group, retaining wildcard rules robotparser misses."""

    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []

    def finish_group() -> None:
        nonlocal agents, rules
        if agents:
            groups.append((agents, rules))
        agents, rules = [], []

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            if rules:
                finish_group()
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if rules:
                finish_group()
            agents.append(value.lower())
        elif field in {"allow", "disallow"} and agents and value:
            rules.append((field == "allow", value))
    finish_group()

    normalized_agent = user_agent.lower()
    selected: list[tuple[bool, str]] = []
    best_score = -1
    for group_agents, group_rules in groups:
        scores = [
            0 if token == "*" else len(token)
            for token in group_agents
            if token == "*" or token in normalized_agent
        ]
        if not scores:
            continue
        score = max(scores)
        if score > best_score:
            selected = list(group_rules)
            best_score = score
        elif score == best_score:
            selected.extend(group_rules)
    return selected


def _rule_decision(
    rules: list[tuple[bool, str]],
    url: str,
) -> RobotsDecision | None:
    parsed = urlsplit(url)
    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"

    matches: list[tuple[int, bool]] = []
    for allowed, pattern in rules:
        anchored = pattern.endswith("$")
        raw_pattern = pattern[:-1] if anchored else pattern
        expression = re.escape(raw_pattern).replace(r"\*", ".*")
        expression = f"^{expression}{'$' if anchored else ''}"
        if re.search(expression, target):
            specificity = len(raw_pattern.replace("*", ""))
            matches.append((specificity, allowed))
    if not matches:
        return None
    best_specificity = max(item[0] for item in matches)
    # RFC 9309: the most specific rule wins; Allow wins equal-length ties.
    allowed = any(value for score, value in matches if score == best_specificity)
    return RobotsDecision.ALLOWED if allowed else RobotsDecision.DISALLOWED
