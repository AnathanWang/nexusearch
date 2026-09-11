"""LLM grounded search adapter: citations → hits only, never invented domains."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence

from nexusearch.discovery import extract_domain
from nexusearch.llm_protocol import LlmJsonClient
from nexusearch.models import SearchHit
from nexusearch.url_safety import check_url_host

logger = logging.getLogger(__name__)

PromptFactory = Callable[[str], str]

_GROUNDED_PROMPT = """\
You are a web research assistant with live browsing. Answer ONLY with JSON.

Task: find real websites relevant to this research query:
---
{query}
---

Rules:
- Every candidate MUST include a real URL you actually found (https only).
- Never invent a company name or domain. If unsure, omit the candidate.
- Prefer official company sites over aggregators.

Return exactly:
{"candidates": [{"name": "...", "url": "https://...", "why": "...", "confidence": 0.7}]}
"""


def default_grounded_prompt(query: str) -> str:
    return _GROUNDED_PROMPT.replace("{query}", query.replace("{", "(").replace("}", ")"))


class LlmGroundedAdapter:
    """
    Discovery via a grounded LLM (Perplexity sonar / OpenAI responses+web_search /
    Gemini grounding — anything behind LlmJsonClient).

    Hard gate: a candidate becomes a SearchHit only with a valid https URL that
    passes check_url_host and the profile ignore list. Entities without URL go to
    `discarded_entities`, never to hits.
    """

    channel = "llm_grounded"

    def __init__(
        self,
        llm: LlmJsonClient | None,
        *,
        name: str = "llm_grounded",
        prompt_factory: PromptFactory = default_grounded_prompt,
    ) -> None:
        self.llm = llm
        self.name = name
        self.prompt_factory = prompt_factory
        self.discarded_entities: list[dict] = []

    def discover(
        self,
        query: str,
        *,
        max_results: int = 10,
        iteration: int = 1,
        ignored_domains: Sequence[str] = (),
    ) -> tuple[list[SearchHit], bool]:
        if self.llm is None:
            return [], False

        try:
            data = self.llm.generate_json(self.prompt_factory(query))
        except Exception as e:  # noqa: BLE001
            logger.warning("LlmGrounded(%s) failed for '%s': %s", self.name, query, e)
            return [], False

        candidates = _extract_candidates(data)
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for cand in candidates:
            url = str(cand.get("url") or "").strip()
            name = str(cand.get("name") or "").strip()
            why = str(cand.get("why") or "").strip()
            host = check_url_host(url) if url else None
            if host is None:
                if name or url:
                    self.discarded_entities.append({"name": name, "url": url, "reason": "no_valid_https_url"})
                continue
            from nexusearch.discovery import _matches_ignored  # local util

            if any(_matches_ignored(host, ign) for ign in ignored_domains):
                self.discarded_entities.append({"name": name, "url": url, "reason": "ignored_domain"})
                continue
            if host in seen:
                continue
            seen.add(host)
            conf = cand.get("confidence")
            try:
                conf_val = float(conf) if conf is not None else None
            except (TypeError, ValueError):
                conf_val = None
            hits.append(
                SearchHit(
                    title=name or host,
                    url=url,
                    snippet=why,
                    domain=host,
                    discovery_query=query,
                    iteration=iteration,
                    source_adapter=self.name,
                    channel=self.channel,
                    grounding_score=conf_val,
                )
            )
            if len(hits) >= max_results:
                break
        return hits, True


def _extract_candidates(data) -> list[dict]:  # noqa: ANN001
    if isinstance(data, dict):
        raw = data.get("candidates")
        if isinstance(raw, list):
            return [c for c in raw if isinstance(c, dict)]
        return []
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    if isinstance(data, str):
        m = re.search(r"\{.*\}", data, re.S)
        if m:
            try:
                return _extract_candidates(json.loads(m.group(0)))
            except json.JSONDecodeError:
                return []
    return []
