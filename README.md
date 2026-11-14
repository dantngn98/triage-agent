# Analytics Triage Agent

Reads an analytics intake request, gathers context from a ticket board and a data catalog, decides a route, and files one task under the right parent user story once a human approves it.

> Work in progress. See [`docs/PLAN.md`](docs/PLAN.md) for the full spec.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env   # then add GEMINI_API_KEY
.venv\Scripts\python -m pytest
```
