"""Skill router: choose the skill on the server, before calling the LLM.

Without a router the model spends whole round-trips deciding which skill to load and
then reading its files (3 calls). With it, the matched skill's content goes into the
first request and most questions are answered in 1 call. The tools stay available, so a
wrong or missed route only costs one extra call, never a wrong answer.

Matching:
1. Semantic: Gemini embeddings of each skill's description + example questions (FAQ
   headings, learned routing examples) compared with the caller's question.
2. Lexical fallback (no API needed): IDF-weighted keyword match (router/lexical.py);
   character bigrams make it work for Khmer, which has no spaces between words.
3. Follow-ups: a short message with no clear match stays on the previous turn's skill
   ("and for grade 10?").
"""

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from receptionist.router.embeddings import GeminiEmbedder, Vector, cosine, document_text, query_text
from receptionist.router.lexical import LexicalIndex
from receptionist.skills.loader import SkillRegistry

logger = logging.getLogger(__name__)

FOLLOW_UP_MAX_WORDS = 8
LEXICAL_MARGIN = 0.1
# Unambiguous keyword match (e.g. "scholarship"): skip the ~0.6 s embedding call.
LEXICAL_SURE_SCORE = 0.8
LEXICAL_SURE_MARGIN = 0.3
QUERY_CACHE_SIZE = 512


@dataclass
class RouteResult:
    skill: str | None
    score: float
    method: str  # "embedding" | "lexical" | "follow-up" | "none"
    margin: float = 0.0  # lead over the second-best skill


class SkillRouter:
    def __init__(
        self,
        skills: SkillRegistry,
        embedder: GeminiEmbedder | None = None,
        threshold: float = 0.65,
        margin: float = 0.03,
        lexical_threshold: float = 0.35,
        extra_examples: Callable[[], dict[str, list[str]]] | None = None,
    ):
        self.skills = skills
        self.embedder = embedder
        self.threshold = threshold
        self.margin = margin
        self.lexical_threshold = lexical_threshold
        self._query_cache: OrderedDict[str, Vector] = OrderedDict()
        # Learned routing examples (Phase 5), loaded on each index build.
        self.extra_examples = extra_examples or (lambda: {})
        self._index: list[tuple[str, Vector]] = []
        self._lexical = LexicalIndex({})
        self._built_for: tuple[int, int] | None = None
        self._examples_version = 0
        self._lock = asyncio.Lock()
        self._embeddings_failed = False

    def examples_changed(self) -> None:
        """Call when learned routing examples change, to rebuild the index."""
        self._examples_version += 1

    def _documents(self) -> dict[str, list[str]]:
        extra = self.extra_examples()
        docs: dict[str, list[str]] = {}
        for name in self.skills.names():
            skill = self.skills.get(name)
            docs[name] = [
                skill.description,
                *self.skills.example_questions(name),
                *extra.get(name, []),
            ]
        return docs

    async def ensure_index(self) -> None:
        key = (self.skills.version, self._examples_version)
        if self._built_for == key:
            return
        async with self._lock:
            if self._built_for == key:
                return
            docs = self._documents()
            self._lexical = LexicalIndex(docs)
            index: list[tuple[str, Vector]] = []
            if self.embedder is not None:
                labels, texts = [], []
                for name, items in docs.items():
                    for item in items:
                        labels.append(name)
                        texts.append(document_text(name, item))
                try:
                    vectors = await self.embedder.embed(texts)
                    index = list(zip(labels, vectors, strict=True))
                    self._embeddings_failed = False
                    logger.info("router index built: %d examples, %d skills", len(index), len(docs))
                except Exception as exc:
                    self._embeddings_failed = True
                    logger.warning("router embeddings unavailable (%s); using lexical", exc)
            self._index = index
            self._query_cache.clear()
            self._built_for = key

    async def _query_vector(self, question: str) -> Vector:
        key = question.strip().lower()
        if key in self._query_cache:
            self._query_cache.move_to_end(key)
            return self._query_cache[key]
        (vec,) = await self.embedder.embed([query_text(question)])
        self._query_cache[key] = vec
        if len(self._query_cache) > QUERY_CACHE_SIZE:
            self._query_cache.popitem(last=False)
        return vec

    async def _embedding_route(self, question: str) -> RouteResult | None:
        if not self._index or self.embedder is None:
            return None
        try:
            qvec = await self._query_vector(question)
        except Exception as exc:
            logger.warning("query embedding failed (%s); using lexical", exc)
            return None
        best: dict[str, float] = {}
        for name, vec in self._index:
            score = cosine(qvec, vec)
            if score > best.get(name, -1.0):
                best[name] = score
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        skill, score = ranked[0]
        margin = score - (ranked[1][1] if len(ranked) > 1 else 0.0)
        # Calibrated on real questions: absolute scores of matches and non-matches overlap,
        # but true matches are clearly ahead of the runner-up skill.
        routed = score >= self.threshold and margin >= self.margin
        return RouteResult(skill if routed else None, score, "embedding", margin)

    def _lexical_route(self, question: str) -> RouteResult:
        scores = sorted(self._lexical.scores(question).items(), key=lambda kv: -kv[1])
        if not scores:
            return RouteResult(None, 0.0, "lexical")
        best_skill, best_score = scores[0]
        margin = best_score - (scores[1][1] if len(scores) > 1 else 0.0)
        # Keyword scores are coarse: only route when clearly ahead of the next skill.
        if best_score >= self.lexical_threshold and margin >= LEXICAL_MARGIN:
            return RouteResult(best_skill, best_score, "lexical", margin)
        return RouteResult(None, best_score, "lexical", margin)

    async def route(self, question: str, previous_skill: str | None = None) -> RouteResult:
        await self.ensure_index()
        lexical = self._lexical_route(question)
        # 1) Unambiguous keywords: answer routing instantly, no API call.
        if (
            lexical.skill
            and lexical.score >= LEXICAL_SURE_SCORE
            and lexical.margin >= LEXICAL_SURE_MARGIN
        ):
            return lexical
        # 2) Semantic match (handles paraphrases and Khmer well).
        semantic = await self._embedding_route(question)
        if semantic is not None and semantic.skill:
            return semantic
        # 3) Weaker but clear keyword match.
        if lexical.skill:
            return lexical
        # 4) Short follow-up with no topic of its own: stay on the previous skill.
        score = semantic.score if semantic else lexical.score
        if previous_skill and self.skills.get(previous_skill):
            if len(question.split()) <= FOLLOW_UP_MAX_WORDS:
                return RouteResult(previous_skill, score, "follow-up")
        return RouteResult(None, score, "none")
