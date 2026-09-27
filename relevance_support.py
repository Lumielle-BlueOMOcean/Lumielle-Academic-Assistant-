"""Deterministic multilingual terms shared by research and literature retrieval."""

from __future__ import annotations

import re


def multilingual_terms(text):
    """Return stable English/numeric tokens and Chinese character bigrams."""
    value = str(text or "").casefold()
    terms = {
        token for token in re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)*", value)
        if len(token) > 1 or token.isdigit()
    }
    for sequence in re.findall(r"[\u3400-\u9fff]+", value):
        if len(sequence) == 1:
            terms.add(sequence)
        else:
            terms.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return terms
