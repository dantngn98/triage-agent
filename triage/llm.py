"""The only module that imports google-genai.

Surface confirmed by the step 1 spike: client.models.generate_content with
manual function calling and response_schema. Callers see SDK-free types
(ToolCall, ContextTurn, TriageOutput, CallUsage) plus an opaque history list,
so swapping providers touches this file only.

No temperature, top_p or top_k is ever sent; thinking_level only.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from google import genai
from google.genai import errors, types
from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from triage.config import get_settings
from triage.render import context_system_prompt, triage_system_prompt
from triage.schemas import CallUsage, TriageResult

RETRY_CODES = {429, 503}
MAX_ATTEMPTS = 5


@dataclass
class ToolCall:
    id: str | None
    name: str
    args: dict[str, Any]


@dataclass
class ContextTurn:
    calls: list[ToolCall]
    text: str
    usage: CallUsage


@dataclass
class TriageOutput:
    parsed: TriageResult | None
    raw: str
    usage: CallUsage
    parse_error: str | None = None


@dataclass
class History:
    """Opaque conversation state for the context loop."""
    contents: list[Any] = field(default_factory=list)


# --------------------------------------------------------------------------
# Client, retries and pacing
# --------------------------------------------------------------------------
_client: genai.Client | None = None
_pace_lock = threading.Lock()
_last_call = 0.0


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        key = get_settings().gemini_api_key.get_secret_value()
        if not key:
            raise RuntimeError("GEMINI_API_KEY is empty. Set it in .env.")
        _client = genai.Client(api_key=key)
    return _client


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, errors.APIError) and getattr(exc, "code", None) in RETRY_CODES


def _pace() -> None:
    """Client-side limiter: at most rpm_limit calls per minute (0 disables)."""
    global _last_call
    rpm = get_settings().rpm_limit
    if rpm <= 0:
        return
    with _pace_lock:
        wait = _last_call + 60.0 / rpm - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


@retry(retry=retry_if_exception(_is_retryable),
       wait=wait_random_exponential(multiplier=1, max=30),
       stop=stop_after_attempt(MAX_ATTEMPTS), reraise=True)
def _generate(contents: Any, config: types.GenerateContentConfig) -> types.GenerateContentResponse:
    _pace()
    return _get_client().models.generate_content(
        model=get_settings().model_id, contents=contents, config=config)


def _usage(resp: types.GenerateContentResponse, call: str, latency: float) -> CallUsage:
    m = resp.usage_metadata

    def count(name: str) -> int:
        return (getattr(m, name, None) or 0) if m is not None else 0

    return CallUsage(
        call=call,
        prompt_tokens=count("prompt_token_count"),
        tool_use_prompt_tokens=count("tool_use_prompt_token_count"),
        candidates_tokens=count("candidates_token_count"),
        thoughts_tokens=count("thoughts_token_count"),
        latency_s=round(latency, 3),
    )


def _text(content: types.Content | None) -> str:
    """Join visible text parts; skips thought parts and function calls."""
    if content is None or not content.parts:
        return ""
    return "".join(p.text for p in content.parts if p.text and not p.thought).strip()


# --------------------------------------------------------------------------
# Context loop
# --------------------------------------------------------------------------
def new_history(request_text: str) -> History:
    return History([types.Content(role="user", parts=[types.Part.from_text(text=request_text)])])


def add_tool_results(history: History, results: list[tuple[ToolCall, dict[str, Any]]]) -> None:
    """Append one user turn holding a function response for every call."""
    parts = [
        types.Part(function_response=types.FunctionResponse(
            id=call.id, name=call.name, response=response))
        for call, response in results
    ]
    history.contents.append(types.Content(role="user", parts=parts))


def context_turn(history: History, tools: list[Callable], thinking: str,
                 allow_tools: bool = True, max_turns: int = 4) -> ContextTurn:
    """One model turn of the context loop; appends the model's reply to history.

    With allow_tools=False the model must answer in text (used on the last turn).
    """
    config = types.GenerateContentConfig(
        system_instruction=context_system_prompt(max_turns),
        tools=tools,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
            mode=types.FunctionCallingConfigMode.AUTO if allow_tools
            else types.FunctionCallingConfigMode.NONE)),
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
    )
    t0 = time.perf_counter()
    resp = _generate(history.contents, config)
    usage = _usage(resp, "context", time.perf_counter() - t0)

    content = resp.candidates[0].content if resp.candidates else None
    if content is not None:
        history.contents.append(content)   # unchanged, keeps thought signatures
    calls = [ToolCall(id=fc.id, name=fc.name or "", args=dict(fc.args or {}))
             for fc in (resp.function_calls or [])] if allow_tools else []
    return ContextTurn(calls=calls, text=_text(content), usage=usage)


# --------------------------------------------------------------------------
# Triage call
# --------------------------------------------------------------------------
def triage(prompt: str, thinking: str) -> TriageOutput:
    config = types.GenerateContentConfig(
        system_instruction=triage_system_prompt(),
        response_mime_type="application/json",
        response_schema=TriageResult,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
    )
    t0 = time.perf_counter()
    resp = _generate(prompt, config)
    usage = _usage(resp, "triage", time.perf_counter() - t0)
    content = resp.candidates[0].content if resp.candidates else None
    raw = _text(content)

    parsed = resp.parsed if isinstance(resp.parsed, TriageResult) else None
    error = None
    if parsed is None:
        try:
            parsed = TriageResult.model_validate_json(raw)
        except ValidationError as exc:
            error = f"Output did not parse as TriageResult: {exc.error_count()} error(s): " + "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'root'}: {e['msg']}"
                for e in exc.errors(include_url=False)[:5])
    return TriageOutput(parsed=parsed, raw=raw, usage=usage, parse_error=error)
