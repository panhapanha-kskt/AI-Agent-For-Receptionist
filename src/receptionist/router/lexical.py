"""Lexical (keyword) skill matching, used when embeddings are unavailable.

TF-IDF-style scoring: a query word that appears in many skills (e.g. "school") counts for
little; a rare, specific word (e.g. "tuition", "scholarship") counts for a lot.

Tokens:
- Latin script: lower-cased words with light stemming ("payments" -> "pay"), minus
  common stop words.
- Khmer script: character bigrams, because Khmer is written without spaces between words.
"""

import math
import re
import unicodedata

_KHMER_RUN = re.compile(r"[ក-៿]+")
_WORD = re.compile(r"[a-z0-9]+")
STOP_WORDS = {
    "a", "an", "and", "are", "at", "be", "can", "do", "does", "for", "from", "have", "how",
    "i", "if", "in", "is", "it", "me", "my", "of", "on", "or", "our", "please", "should",
    "so", "the", "there", "this", "to", "we", "what", "when", "where", "which", "who",
    "why", "will", "with", "you", "your", "would", "could", "about", "any", "get", "need",
    "want", "tell", "know", "hi", "hello", "thanks", "thank", "much", "many",
}  # fmt: skip


def _stem(word: str) -> str:
    for suffix, repl in (
        ("sses", "ss"),
        ("ches", "ch"),
        ("shes", "sh"),
        ("xes", "x"),
        ("ies", "y"),
    ):
        if word.endswith(suffix) and len(word) > len(suffix) + 1:
            return word[: -len(suffix)] + repl
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        word = word[:-1]
    for suffix in ("ment", "ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFC", text).lower()
    out = {_stem(w) for w in _WORD.findall(text) if w not in STOP_WORDS and len(w) > 1}
    for run in _KHMER_RUN.findall(text):
        out.update(run[i : i + 2] for i in range(len(run) - 1))
    return out


class LexicalIndex:
    def __init__(self, documents: dict[str, list[str]]):
        self.bags = {name: set().union(*(tokens(d) for d in docs)) if docs else set()
                     for name, docs in documents.items()}  # fmt: skip
        n = max(1, len(self.bags))
        df: dict[str, int] = {}
        for bag in self.bags.values():
            for tok in bag:
                df[tok] = df.get(tok, 0) + 1
        self.idf = {tok: math.log(1 + n / count) for tok, count in df.items()}
        self.unknown_idf = math.log(1 + n)  # words no skill mentions

    def scores(self, query: str) -> dict[str, float]:
        q = tokens(query)
        if not q:
            return {name: 0.0 for name in self.bags}
        total = sum(self.idf.get(t, self.unknown_idf) for t in q)
        return {name: sum(self.idf[t] for t in q & bag) / total for name, bag in self.bags.items()}
