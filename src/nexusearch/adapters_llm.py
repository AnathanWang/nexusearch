"""LLM grounded search adapter: citations → hits only, never invented domains."""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from collections import deque
from collections.abc import Callable, Sequence

from nexusearch.discovery import matches_ignored
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
        max_discarded: int = 500,
    ) -> None:
        self.llm = llm
        self.name = name
        self.prompt_factory = prompt_factory
        self._discarded: deque[dict] = deque(maxlen=max_discarded)
        self._lock = threading.Lock()

    @property
    def discarded_entities(self) -> list[dict]:
        """Snapshot of recently discarded candidates (thread-safe, bounded)."""
        with self._lock:
            return list(self._discarded)

    def _discard(self, entry: dict) -> None:
        with self._lock:
            self._discarded.append(entry)

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
            if host is not None:
                host = host.removeprefix("www.")
            if host is None:
                if name or url:
                    self._discard({"name": name, "url": url, "query": query, "reason": "no_valid_https_url"})
                continue
            if any(matches_ignored(host, ign) for ign in ignored_domains):
                self._discard({"name": name, "url": url, "query": query, "reason": "ignored_domain"})
                continue
            if host in seen:
                continue
            seen.add(host)
            conf = cand.get("confidence")
            try:
                conf_val = float(conf) if conf is not None else None
            except (TypeError, ValueError):
                conf_val = None
            if conf_val is not None:
                if not math.isfinite(conf_val):
                    conf_val = None
                else:
                    conf_val = max(0.0, min(1.0, conf_val))
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


def _extract_candidates(data) -> list[dict]:
    if isinstance(data, dict):
        raw = data.get("candidates")
        if isinstance(raw, list):
            return [c for c in raw if isinstance(c, dict)]
        return []
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    if isinstance(data, str):
        try:
            return _extract_candidates(json.loads(data))
        except json.JSONDecodeError:
            pass
        m = re.search(r"\[.*\]|\{.*\}", data, re.DOTALL)
        if m:
            try:
                return _extract_candidates(json.loads(m.group(0)))
            except json.JSONDecodeError:
                return []
    return []
