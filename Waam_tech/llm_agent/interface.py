"""Lightweight, offline-only assistant helpers."""

from __future__ import annotations

from typing import List, Optional

from .context_builder import build_context, context_as_text

HistoryItem = dict


def _format_history(history: Optional[List[HistoryItem]]) -> str:
    if not history:
        return ""
    return "\n".join(f"{item.get('role', 'user')}: {item.get('content', '')}" for item in history)


def gather_default_context(include_config: bool = True) -> str:
    ctx = build_context(include_config=include_config)
    return context_as_text(ctx)


def _summarize_context(context: dict) -> str:
    if context.get("error"):
        return context["error"]

    lines: List[str] = []
    doc = context.get("document", {})
    if doc:
        lines.append(f"Document: {doc.get('label') or doc.get('name')}")
    solids = context.get("solids") or []
    lines.append(f"Solids detected: {len(solids)}")

    job = context.get("job") or {}
    operations = job.get("operations") or []
    if operations:
        lines.append(f"Existing Path operations: {len(operations)}")

    selections = context.get("selection") or []
    if selections:
        lines.append(f"Active selection items: {len(selections)}")

    waam_plan = context.get("waam_plan_path")
    if waam_plan:
        lines.append(f"Latest WAAM plan: {waam_plan}")

    features_path = context.get("features_path")
    if features_path:
        lines.append(f"Features JSON: {features_path}")

    ops_path = context.get("operations_path")
    if ops_path:
        lines.append(f"Operations JSON: {ops_path}")

    return "\n".join(lines)


def chat_with_llm(
    prompt: str,
    include_context: bool = True,
    history: Optional[List[HistoryItem]] = None,
) -> str:
    prompt = (prompt or "").strip()
    context = build_context(include_config=include_context)

    sections: List[str] = []
    summary = _summarize_context(context)
    if summary:
        sections.append(summary)

    if history:
        sections.append("Conversation so far:\n" + _format_history(history))

    sections.append(
        "Assistant (offline mode): This minimal build does not contact an external LLM. "
        "Use the summary above to interpret your model and plan subsequent WAAM steps."
    )

    if prompt:
        sections.append(f"Your prompt: {prompt}")

    return "\n\n".join(part for part in sections if part)
