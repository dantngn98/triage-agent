"""Read-only tools the model may call in the context loop, and their dispatcher.

The tool functions are plain typed callables whose docstrings become the tool
descriptions the model sees (llm.py turns them into function declarations).
They are built per board by `read_tools`, so the model-facing signature holds
only the model's arguments. Write actions are not here: they live in Python
and run only after approval.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import ValidationError, validate_call

from triage.board import Board, TicketNotFound, tokenize
from triage.config import ROOT

CATALOG_PATH = ROOT / "data" / "catalog.yaml"
MAX_QUERY_CHARS = 200
MAX_LIMIT = 10
MAX_CATALOG_RESULTS = 5
READ_TOOL_NAMES = ("search_tickets", "get_ticket", "read_catalog")


def _clean_query(query: str) -> str:
    return query[:MAX_QUERY_CHARS]


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------
@lru_cache(maxsize=4)
def _load_catalog(path: str) -> tuple[dict, ...]:
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["entries"]
    return tuple(entries)


def load_catalog(path: Path | str = CATALOG_PATH) -> list[dict]:
    return [dict(e) for e in _load_catalog(str(path))]


def catalog_names(path: Path | str = CATALOG_PATH) -> set[str]:
    """Exact entry names, for the validator's grounding check."""
    return {e["name"] for e in _load_catalog(str(path))}


def search_catalog(query: str, path: Path | str = CATALOG_PATH) -> list[dict]:
    """Case-insensitive keyword match over entry names and key fields.

    Crude on purpose: descriptions are not searched, so a near-miss here is a
    realistic grounding failure worth measuring.
    """
    q = tokenize(_clean_query(query))
    if not q:
        return []
    scored = []
    for e in _load_catalog(str(path)):
        hits = len(q & tokenize(f"{e['name']} {' '.join(e['key_fields'])}"))
        if hits:
            scored.append((hits, e))
    scored.sort(key=lambda he: (-he[0], he[1]["name"]))
    return [
        {k: e[k] for k in ("name", "type", "owner", "refresh", "description", "key_fields")}
        for _, e in scored[:MAX_CATALOG_RESULTS]
    ]


# --------------------------------------------------------------------------
# Tools bound to a board
# --------------------------------------------------------------------------
def read_tools(board: Board, catalog_path: Path | str = CATALOG_PATH) -> dict[str, Callable]:
    """Build the three read tools for one board. Keys are the tool names."""

    def search_tickets(query: str, limit: int = 5) -> list[dict]:
        """Search the analytics ticket board for existing tickets similar to the request.

        Call this first, before deciding anything, to check whether the request
        duplicates or overlaps an existing ticket. Use a few distinctive keywords
        from the request (metric, data source, device model, firmware version),
        not the whole request text. Call it again with different keywords if the
        first search finds nothing relevant.

        Args:
            query: Keywords describing the request, at most 200 characters.
            limit: Maximum number of results, 1 to 10. Defaults to 5.

        Returns:
            Matching tickets with ref, title, status, parent_story and a snippet,
            best match first. An empty list means no ticket shares any keyword.
        """
        results = board.search(_clean_query(query), _clamp_limit(limit))
        return [r.model_dump(mode="json") for r in results]

    def get_ticket(ref: int) -> dict:
        """Get the full details of one ticket on the board by its numeric ref.

        Call this when a search result looks like a possible duplicate, to read
        its full description and status before judging whether the request asks
        for the same work.

        Args:
            ref: The ticket's numeric ref, as returned by search_tickets.

        Returns:
            The ticket's ref, title, description, status, parent_story, created
            date and notes.
        """
        t = board.get(ref)
        return t.model_dump(mode="json", include={
            "ref", "title", "description", "status", "parent_story", "created", "notes"})

    def read_catalog(query: str) -> list[dict]:
        """Look up dashboards and data sources in the analytics data catalog.

        Call this for every dashboard, data source, metric or device feature the
        request names or implies, to check what already exists before proposing
        new work. Matching is by keyword against entry names and field names, so
        try short queries such as "battery firmware" or "fall alert".

        Args:
            query: A few keywords, at most 200 characters.

        Returns:
            Up to 5 matching entries with name, type (dashboard or source), owner,
            refresh cadence, description and key_fields. An empty list means the
            catalog has nothing matching those keywords.
        """
        return search_catalog(query, catalog_path)

    return {f.__name__: f for f in (search_tickets, get_ticket, read_catalog)}


def dispatch_read(name: str, args: dict[str, Any] | None, board: Board,
                  catalog_path: Path | str = CATALOG_PATH) -> dict[str, Any]:
    """Run one read tool by name. Never raises: failures come back as {"error": ...}.

    The returned dict is what the pipeline sends back as the function response.
    """
    tools = read_tools(board, catalog_path)
    fn = tools.get(name)
    if fn is None:
        return {"error": f"Unknown tool '{name}'. Available tools: {', '.join(READ_TOOL_NAMES)}."}
    try:
        return {"result": validate_call(fn)(**(args or {}))}
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
            for e in exc.errors(include_url=False))
        return {"error": f"Bad arguments for {name}: {problems}"}
    except TicketNotFound as exc:
        return {"error": f"No ticket with ref {exc.args[0]}."}
    except Exception as exc:  # noqa: BLE001 - a tool failure must not crash the loop
        return {"error": f"{name} failed: {type(exc).__name__}: {exc}"}
