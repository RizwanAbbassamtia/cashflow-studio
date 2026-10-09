"""Pure text helpers shared by the stages, the gates and the mock.

Everything here is deterministic and has no side effects: sentence splitting, word counts,
tokens, n-gram overlap (the originality maths), title similarity and keyword extraction.
"""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

STOPWORDS = frozenset(
    """a an and are as at be been but by can did do for from had has have he her his how i if
    in into is it its just me my no not of on one or our out she so than that the their them
    then there these they this to too up us was we were what when where which who why will with
    would you your about after again all also any because before being between both could does
    down during each few get got here him himself more most much new now off once only other
    over own same should some such through under until very while el la los las de del que y en
    un una por para con se su al es lo como mas o das dos das uma um para com nao""".split()
)
ABBREVIATIONS = frozenset(
    "mr mrs ms dr prof sr jr st vs etc e.g i.e no approx mt ft inc ltd co".split()
)

WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
SENTENCE_RE = re.compile(r".+?(?:[.!?…]+[\"”’')\]]*(?=\s|$)|$)", re.DOTALL)


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text or ""))


def tokenize(text: str) -> list[str]:
    """Lower-case word tokens; punctuation dropped. Works for any script Python calls ``\\w``."""
    return [t.lower() for t in TOKEN_RE.findall(text or "")]


def split_sentences(text: str) -> list[str]:
    """Split a paragraph into spoken sentences on . ! ? … (closing quotes stay attached).

    A piece that ends in a common abbreviation (``Mr.``, ``Dr.``, ``e.g.``) is joined to the
    next one; the few remaining false splits are harmless for narration.
    """
    flat = re.sub(r"\s+", " ", (text or "").strip())
    if not flat:
        return []
    pieces = [m.group(0).strip() for m in SENTENCE_RE.finditer(flat) if m.group(0).strip()]
    merged: list[str] = []
    for piece in pieces:
        if merged and _ends_with_abbreviation(merged[-1]):
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


def _ends_with_abbreviation(sentence: str) -> bool:
    match = re.search(r"([A-Za-z.]+)\.$", sentence)
    if not match:
        return False
    word = match.group(1).lower().rstrip(".")
    return word in ABBREVIATIONS or (len(word) == 1 and word.isalpha())


def ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    if n <= 0 or len(tokens) < n:
        return []
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def ngram_overlap(candidate: str, reference: str, n: int = 8) -> float:
    """Share of the candidate's n-word sequences that also occur in the reference (0-1).

    Counted over every position in the candidate (a copied sentence repeated twice counts
    twice). A candidate shorter than ``n`` words has no sequences and scores 0.
    """
    cand = ngrams(tokenize(candidate), n)
    if not cand:
        return 0.0
    ref = set(ngrams(tokenize(reference), n))
    if not ref:
        return 0.0
    hits = sum(1 for gram in cand if gram in ref)
    return round(hits / len(cand), 4)


def fingerprint(text: str) -> str:
    """Stable id of a text's words (case and punctuation ignored)."""
    return hashlib.sha256(" ".join(tokenize(text)).encode("utf-8")).hexdigest()


def similarity(a: str, b: str) -> float:
    """difflib ratio on lower-cased, whitespace-normalised text, 0-1."""
    left = re.sub(r"\s+", " ", (a or "").strip().lower())
    right = re.sub(r"\s+", " ", (b or "").strip().lower())
    if not left or not right:
        return 0.0
    return round(SequenceMatcher(None, left, right).ratio(), 4)


def keywords_of(text: str, limit: int = 6, min_length: int = 3) -> list[str]:
    """Meaningful words of a title, in order, without stopwords and duplicates."""
    seen: list[str] = []
    lowered: set[str] = set()
    for token in re.findall(r"[^\W_][\w'’-]*", text or "", re.UNICODE):
        word = token.strip("'’-")
        key = word.lower()
        if len(word) < min_length or key in STOPWORDS or key in lowered:
            continue
        seen.append(word)
        lowered.add(key)
        if len(seen) >= limit:
            break
    return seen


def contains_keyword(text: str, keyword: str) -> bool:
    """True when ``keyword`` (or a word sharing its first five letters) occurs in ``text``."""
    key = keyword.lower()
    tokens = tokenize(text)
    if key in tokens:
        return True
    if len(key) >= 5:
        stem = key[:5]
        return any(t.startswith(stem) for t in tokens)
    return False


def keyword_phrase(text: str, limit: int = 3) -> str:
    """Up to ``limit`` key words of a sentence, in order, title-cased: a popup paraphrase."""
    words = keywords_of(text, limit=limit, min_length=4) or keywords_of(text, limit=limit)
    return " ".join(w[:1].upper() + w[1:] for w in words)


def normalise(text: str | None) -> str:
    return " ".join(tokenize(text or ""))
