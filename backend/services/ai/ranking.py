# Okapi BM25 passage ranking for picking manual pages and text excerpts

import math
import re
from collections import Counter

# Keeps part numbers, oil grades and thread sizes whole: 15400-plm-a02, 5w-30, m12x1.25
_TOKEN = re.compile(r'[a-z0-9]+(?:[-./][a-z0-9]+)*')
_STOP = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has',
    'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may',
    'might', 'can', 'for', 'of', 'to', 'in', 'on', 'at', 'by', 'from',
    'with', 'and', 'or', 'but', 'if', 'what', 'how', 'where', 'when',
    'which', 'who', 'that', 'this', 'i', 'my', 'me', 'it', 'its', 'there',
    'about', 'need', 'find', 'tell', 'show', 'car', 'vehicle',
}


def _terms(text: str) -> list[str]:
    terms = []
    for tok in _TOKEN.findall(text.lower()):
        terms.append(tok)
        if not tok.isalnum():
            terms.extend(p for p in re.split(r'[-./]', tok) if p)
    return terms


def rank_passages(passages: list[str], question: str, k1: float = 1.5, b: float = 0.75) -> list[int]:
    """Indexes of passages that match the question, best first, scored with Okapi BM25."""
    query = {t for t in _terms(question) if t not in _STOP and (len(t) > 2 or any(c.isdigit() for c in t))}
    if not query or not passages:
        return []
    counts = [Counter(_terms(p)) for p in passages]
    lengths = [sum(c.values()) for c in counts]
    avg_len = (sum(lengths) / len(lengths)) or 1
    n = len(passages)
    idf = {}
    for t in query:
        df = sum(1 for c in counts if t in c)
        idf[t] = math.log(1 + (n - df + 0.5) / (df + 0.5))

    scored = []
    for i, c in enumerate(counts):
        score = sum(
            idf[t] * c[t] * (k1 + 1) / (c[t] + k1 * (1 - b + b * lengths[i] / avg_len))
            for t in query if c[t]
        )
        if score > 0:
            scored.append((score, i))
    return [i for _, i in sorted(scored, key=lambda x: -x[0])]


def text_chunks(text: str, size: int = 2000, overlap: int = 400) -> list[str]:
    return [text[i:i + size] for i in range(0, max(len(text) - overlap, 1), size - overlap)]
