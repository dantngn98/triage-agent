"""Deterministic checks on a TriageResult: what the schema cannot express.

Each rule returns a list of error strings (empty when it passes). The strings
are fed back to the model on the retry, so they say what to fix.
"""
from __future__ import annotations

import re
from pathlib import Path
from triage.board import Board, TicketNotFound
from triage.schemas import ROUTE_FIELD_TARGETS, ROUTE_FIELDS, FieldRule, TriageResult
from triage.tools import CATALOG_PATH, catalog_names

MAX_TITLE_CHARS = 80
MAX_DESCRIPTION_WORDS = 120
_LIST_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def check_parse(result: TriageResult | None, parse_error: str | None = None) -> list[str]:
    if result is None:
        return [parse_error or "Output did not parse as TriageResult."]
    return []


def check_route_consistency(result: TriageResult) -> list[str]:
    errors = []
    rule = ROUTE_FIELDS[result.route]
    for column, fields in ROUTE_FIELD_TARGETS.items():
        expected: FieldRule = getattr(rule, column)
        for name in fields:
            value = getattr(result, name)
            filled = value is not None and (not isinstance(value, str) or value.strip() != "")
            if expected is FieldRule.REQUIRED and not filled:
                errors.append(f"Route '{result.route.value}' requires {name} to be filled.")
            elif expected is FieldRule.EMPTY and value != "":
                errors.append(f"Route '{result.route.value}' requires {name} to be an empty string.")
            elif expected is FieldRule.NULL and value is not None:
                errors.append(f"Route '{result.route.value}' requires {name} to be null.")
    return errors


def check_single_paragraph(result: TriageResult) -> list[str]:
    text = result.task_description
    if not text:
        return []
    errors = []
    if "\n" in text or "\r" in text:
        errors.append("task_description must be one paragraph with no line breaks.")
    if any(_LIST_LINE.match(line) for line in text.splitlines()):
        errors.append("task_description must not use bullet points or numbered steps.")
    words = len(text.split())
    if words > MAX_DESCRIPTION_WORDS:
        errors.append(f"task_description has {words} words; keep it to {MAX_DESCRIPTION_WORDS} or fewer.")
    return errors


def check_title_length(result: TriageResult) -> list[str]:
    n = len(result.task_title)
    if n > MAX_TITLE_CHARS:
        return [f"task_title has {n} characters; keep it to {MAX_TITLE_CHARS} or fewer."]
    return []


def check_duplicate_exists(result: TriageResult, board: Board) -> list[str]:
    if result.duplicate_of is None:
        return []
    try:
        board.get(result.duplicate_of)
    except TicketNotFound:
        return [f"duplicate_of {result.duplicate_of} is not a ticket on the board; "
                "use a ref from the context notes."]
    return []


def check_catalog_grounding(result: TriageResult,
                            catalog_path: Path | str = CATALOG_PATH) -> list[str]:
    known = catalog_names(catalog_path)
    unknown = [ref for ref in result.catalog_refs if ref not in known]
    if unknown:
        return [f"catalog_refs contains names not in the catalog: {', '.join(repr(u) for u in unknown)}. "
                "Use exact names from the context notes or drop them."]
    return []


def check_caveats_present(result: TriageResult) -> list[str]:
    if not result.caveats_and_pushback.strip():
        return ["caveats_and_pushback is empty; write the caveats, or 'None' if there are none."]
    return []


def validate(result: TriageResult | None, board: Board, *, parse_error: str | None = None,
             catalog_path: Path | str = CATALOG_PATH) -> list[str]:
    """Run every rule. A parse failure short-circuits, since nothing else can be checked."""
    errors = check_parse(result, parse_error)
    if errors:
        return errors
    return (
        check_route_consistency(result)
        + check_single_paragraph(result)
        + check_title_length(result)
        + check_duplicate_exists(result, board)
        + check_catalog_grounding(result, catalog_path)
        + check_caveats_present(result)
    )
