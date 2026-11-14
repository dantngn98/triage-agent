# Analytics Triage Agent

Coursework agent that triages analytics intake requests: context loop over read-only tools → structured triage → deterministic validator → human approval → Python-executed write.

## Standing rules

- `docs/PLAN.md` is the spec; ask before deviating from it.
- Never create or edit `data/labels.csv`.
- All data is synthetic; no real company names, people or records.
- Send no `temperature`, `top_p` or `top_k`; use `thinking_level` only.
- Only `triage/llm.py` imports `google-genai`.
- Run `pytest` before every commit; stop at **[You]** checkpoints.
- Never run the eval without the spend check passing.

## Environment

- Windows, Python 3.11+ in `.venv` (`.venv\Scripts\python`).
- Settings come from `.env` (gitignored); `.env.example` is committed.
- Assignment 1's Iteration 3 prompt and few-shot examples live in `docs/assignment1_prompt.md`.
