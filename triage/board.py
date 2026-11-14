"""Board protocol and the JSON-file implementation.

The pipeline only sees `Board`, so a TaigaBoard can replace JsonBoard later
without touching the agent.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from triage.schemas import TASK_ROUTES, Route

SNIPPET_CHARS = 160

STOPWORDS = frozenset("""
a an and are as at be been but by can could do does for from get give go had has have
how i if in into is it its just me my need needs of on or our please so some someone
than that the their them then there these they this to up us want wants was we were what
when where which who why will with would you your
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)*")   # keeps versions like "3.1" whole


def tokenize(text: str) -> set[str]:
    """Lowercase, split into words and version numbers, drop stopwords."""
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS}


class TicketNotFound(KeyError):
    pass


class Story(BaseModel):
    ref: int
    title: str
    route: Route


class Ticket(BaseModel):
    ref: int
    title: str
    description: str
    status: str
    parent_story: Route
    created: str
    tags: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    request_id: str | None = None        # intake request that produced it, if any


class TicketSummary(BaseModel):
    ref: int
    title: str
    status: str
    parent_story: Route
    snippet: str


class BoardData(BaseModel):
    stories: list[Story]
    tickets: list[Ticket]


class Board(Protocol):
    def search(self, query: str, limit: int = 5) -> list[TicketSummary]: ...
    def get(self, ref: int) -> Ticket: ...
    def create_task(self, parent: Route, title: str, description: str,
                    tags: list[str], request_id: str | None = None) -> int: ...
    def append_note(self, ref: int, note: str) -> None: ...


class JsonBoard:
    """A board stored in one JSON file, re-read on every call so writes are never stale."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"Board file not found: {self.path}")

    # -- persistence ------------------------------------------------------
    def _load(self) -> BoardData:
        return BoardData.model_validate_json(self.path.read_text(encoding="utf-8"))

    def _save(self, data: BoardData) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data.model_dump(mode="json"), indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, self.path)

    # -- reads ------------------------------------------------------------
    def search(self, query: str, limit: int = 5) -> list[TicketSummary]:
        """Rank tickets by the share of query tokens found in title + description."""
        q = tokenize(query)
        if not q or limit < 1:
            return []
        scored = []
        for t in self._load().tickets:
            score = len(q & tokenize(f"{t.title} {t.description}")) / len(q)
            if score > 0:
                scored.append((score, t))
        scored.sort(key=lambda st: (-st[0], st[1].ref))
        return [_summary(t) for _, t in scored[:limit]]

    def get(self, ref: int) -> Ticket:
        for t in self._load().tickets:
            if t.ref == ref:
                return t
        raise TicketNotFound(ref)

    # -- writes (called by Python after approval, never by the model) -----
    def create_task(self, parent: Route, title: str, description: str,
                    tags: list[str], request_id: str | None = None) -> int:
        parent = Route(parent)
        if parent not in TASK_ROUTES:
            raise ValueError(f"Tasks can only be filed under {sorted(r.value for r in TASK_ROUTES)}")
        data = self._load()
        ref = max([t.ref for t in data.tickets] + [s.ref for s in data.stories]) + 1
        data.tickets.append(Ticket(
            ref=ref, title=title, description=description, status="Backlog",
            parent_story=parent, created=date.today().isoformat(), tags=list(tags),
            request_id=request_id,
        ))
        self._save(data)
        return ref

    def append_note(self, ref: int, note: str) -> None:
        data = self._load()
        for t in data.tickets:
            if t.ref == ref:
                t.notes.append(note)
                self._save(data)
                return
        raise TicketNotFound(ref)


def _summary(t: Ticket) -> TicketSummary:
    snippet = t.description if len(t.description) <= SNIPPET_CHARS \
        else t.description[:SNIPPET_CHARS].rsplit(" ", 1)[0] + "..."
    return TicketSummary(ref=t.ref, title=t.title, status=t.status,
                         parent_story=t.parent_story, snippet=snippet)


def reset_from_seed(seed_path: Path | str, path: Path | str) -> JsonBoard:
    """Copy the seed board over the working board and return it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(seed_path, path)
    return JsonBoard(path)
