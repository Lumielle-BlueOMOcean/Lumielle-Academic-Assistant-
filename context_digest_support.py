"""Small deterministic helpers for bounded, source-faithful context digests."""

from __future__ import annotations


_EXCERPT_MARKER = " … [balanced source excerpts; omitted detail remains stored] … "


def balanced_excerpt(text, max_chars):
    """Keep exact beginning, middle, and tail excerpts with an explicit omission marker."""
    value = str(text or "")
    try:
        budget = max(0, int(max_chars))
    except (TypeError, ValueError):
        budget = 0
    if len(value) <= budget:
        return value
    if budget == 0:
        return ""
    if budget < 12:
        if budget == 1:
            return value[len(value) // 2]
        indexes = [round(index * (len(value) - 1) / (budget - 1)) for index in range(budget)]
        return "".join(value[index] for index in indexes)
    marker = _EXCERPT_MARKER
    if len(marker) * 2 >= budget:
        marker = " … "
    available = max(0, budget - len(marker) * 2)
    first_size = available // 3
    middle_size = available // 3
    last_size = available - first_size - middle_size
    first = value[:first_size]
    middle_start = max(first_size, len(value) // 2 - middle_size // 2)
    middle = value[middle_start:middle_start + middle_size]
    last = value[-last_size:] if last_size else ""
    return first + marker + middle + marker + last
