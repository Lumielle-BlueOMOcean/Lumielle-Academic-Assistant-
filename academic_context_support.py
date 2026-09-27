"""Assemble per-chapter Research Fact and bound Literature Evidence context."""

from literature_intelligence import format_literature_evidence_for_chapter
from research_support import build_chapter_research_context


def attach_chapter_grounding(context, research_base, literature_evidence, literatures, *, locale="en"):
    result = dict(context)
    research = build_chapter_research_context(
        research_base,
        result.get("current_title", ""),
        result.get("current_desc", ""),
        chapter_id=result.get("current_id"),
        locale=locale,
    )
    result["research_constraints"] = research["research_constraints"]
    result["research_facts"] = research["research_facts"]
    result["selected_research_facts"] = research["selected_research_facts"]
    result["literature_evidence"] = format_literature_evidence_for_chapter(
        literature_evidence,
        result.get("current_refs", []),
        result.get("current_title", ""),
        result.get("current_desc", ""),
        locale=locale,
        literatures=literatures,
    )
    return result
