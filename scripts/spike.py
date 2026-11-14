"""Phase 0 spike: confirm function calling and response_schema on gemini-3.8-flash.

Run from the repo root with the venv active:
    python scripts/spike.py              # generate_content first, Interactions API only if it fails
    python scripts/spike.py --interactions   # force the Interactions API path

Makes at most 3 small API calls per surface (well under $0.01). Sends no
temperature/top_p/top_k; thinking_level only.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from pydantic import BaseModel

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader so the spike does not depend on config.py (step 2)."""
    if not path.exists():
        sys.exit(f"Missing {path}. Copy .env.example to .env and set GEMINI_API_KEY.")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()
MODEL = os.environ.get("MODEL_ID", "gemini-3.8-flash")
THINKING = os.environ.get("THINKING_LEVEL", "low")
if not os.environ.get("GEMINI_API_KEY"):
    sys.exit("GEMINI_API_KEY is empty in .env.")

PROMPT = (
    "An intake request says: 'Please build a dashboard of daily fall-alert events "
    "per firmware version.' Check the ticket board for an existing ticket about "
    "fall alerts before answering."
)

STUB_TICKETS = [
    {
        "ref": 104,
        "title": "Fall-alert events dashboard by firmware",
        "status": "In progress",
        "parent_story": "Dashboard Requests",
        "snippet": "Daily count of fall-alert events split by firmware version.",
    }
]


def search_tickets(query: str, limit: int = 5) -> list[dict]:
    """Search the analytics ticket board for existing tickets similar to a request.

    Call this first to check whether the request duplicates an existing ticket.

    Args:
        query: Keywords describing the request, at most 200 characters.
        limit: Maximum number of results to return, at most 10.
    """
    return STUB_TICKETS[: max(1, min(limit, 10))]


class SpikeResult(BaseModel):
    route: str
    reason: str


def banner(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def dump(obj) -> str:
    if obj is None:
        return "None"
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(exclude_none=True, mode="json")
    return json.dumps(obj, indent=2, default=str)


# --------------------------------------------------------------------------
# Surface A: client.models.generate_content
# --------------------------------------------------------------------------
def spike_generate_content(client: genai.Client) -> None:
    thinking = types.ThinkingConfig(thinking_level=THINKING)

    banner(f"[generate_content] call 1: tool offered ({MODEL}, thinking={THINKING})")
    history = [types.Content(role="user", parts=[types.Part.from_text(text=PROMPT)])]
    tool_cfg = types.GenerateContentConfig(
        tools=[search_tickets],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=thinking,
    )
    t0 = time.perf_counter()
    r1 = client.models.generate_content(model=MODEL, contents=history, config=tool_cfg)
    print(f"latency: {time.perf_counter() - t0:.2f}s")
    calls = r1.function_calls or []
    if not calls:
        raise RuntimeError(f"No function call returned. Text was: {r1.text!r}")
    for c in calls:
        print("function_call:", dump(c))
    print("usage_metadata:", dump(r1.usage_metadata))

    banner("[generate_content] call 2: function response sent back")
    history.append(r1.candidates[0].content)  # keeps thought signatures intact
    parts = []
    for c in calls:
        result = search_tickets(**(c.args or {}))
        parts.append(
            types.Part(
                function_response=types.FunctionResponse(
                    id=c.id, name=c.name, response={"result": result}
                )
            )
        )
    history.append(types.Content(role="user", parts=parts))
    t0 = time.perf_counter()
    r2 = client.models.generate_content(model=MODEL, contents=history, config=tool_cfg)
    print(f"latency: {time.perf_counter() - t0:.2f}s")
    print("text:", r2.text)
    print("further function_calls:", dump(r2.function_calls))
    print("usage_metadata:", dump(r2.usage_metadata))

    banner("[generate_content] call 3: response_schema=SpikeResult")
    t0 = time.perf_counter()
    r3 = client.models.generate_content(
        model=MODEL,
        contents=(
            "Route this request to one of 'Data Requests', 'Dashboard Requests', "
            "'Analytics Requests': 'Build a dashboard of daily fall-alert events "
            "per firmware version.'"
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SpikeResult,
            thinking_config=thinking,
        ),
    )
    print(f"latency: {time.perf_counter() - t0:.2f}s")
    print("raw text:", r3.text)
    print("parsed type:", type(r3.parsed).__name__)
    print("parsed:", dump(r3.parsed))
    print("usage_metadata:", dump(r3.usage_metadata))
    if not isinstance(r3.parsed, SpikeResult):
        raise RuntimeError("response.parsed is not a SpikeResult")


# --------------------------------------------------------------------------
# Surface B: client.interactions.create (fallback)
# --------------------------------------------------------------------------
def spike_interactions(client: genai.Client) -> None:
    gen_cfg = {"thinking_level": THINKING}
    tool_decl = {
        "type": "function",
        "name": "search_tickets",
        "description": search_tickets.__doc__,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    }

    banner(f"[interactions] call 1: tool offered ({MODEL}, thinking={THINKING})")
    i1 = client.interactions.create(
        model=MODEL, input=PROMPT, tools=[tool_decl], generation_config=gen_cfg
    )
    calls = [s for s in (i1.steps or []) if getattr(s, "type", None) == "function_call"]
    if not calls:
        raise RuntimeError(f"No function call step. Steps: {dump(i1.steps)}")
    for c in calls:
        print("function_call:", dump(c))
    print("usage:", dump(i1.usage))

    banner("[interactions] call 2: function result sent back")
    results = [
        {
            "type": "function_result",
            "call_id": c.id,
            "name": c.name,
            "result": json.dumps(search_tickets(**c.arguments)),
        }
        for c in calls
    ]
    i2 = client.interactions.create(
        model=MODEL,
        previous_interaction_id=i1.id,
        input=results,
        tools=[tool_decl],
        generation_config=gen_cfg,
    )
    print("output_text:", i2.output_text)
    print("usage:", dump(i2.usage))

    banner("[interactions] call 3: JSON schema response format")
    i3 = client.interactions.create(
        model=MODEL,
        input=(
            "Route this request to one of 'Data Requests', 'Dashboard Requests', "
            "'Analytics Requests': 'Build a dashboard of daily fall-alert events "
            "per firmware version.'"
        ),
        response_mime_type="application/json",
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": SpikeResult.model_json_schema(),
        },
        generation_config=gen_cfg,
    )
    print("output_text:", i3.output_text)
    parsed = SpikeResult.model_validate_json(i3.output_text or "")
    print("parsed:", dump(parsed))
    print("usage:", dump(i3.usage))


def main() -> int:
    client = genai.Client()  # reads GEMINI_API_KEY
    if "--interactions" not in sys.argv:
        try:
            spike_generate_content(client)
            banner("RESULT: generate_content supports function calling + response_schema")
            return 0
        except Exception as exc:  # noqa: BLE001 - spike reports any failure
            banner(f"generate_content FAILED: {type(exc).__name__}: {exc}")
            print("Falling back to the Interactions API...")
    try:
        spike_interactions(client)
        banner("RESULT: Interactions API supports function calling + JSON schema output")
        return 0
    except Exception as exc:  # noqa: BLE001
        banner(f"interactions FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
