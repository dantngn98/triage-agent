"""Offline tests of llm.py helpers; no network calls."""
from types import SimpleNamespace

from google.genai import errors, types

from triage import llm


def test_usage_reads_fields_and_defaults_missing_to_zero():
    resp = SimpleNamespace(usage_metadata=SimpleNamespace(
        prompt_token_count=169, candidates_token_count=20, thoughts_token_count=None))
    u = llm._usage(resp, "context", 1.23456)
    assert (u.prompt_tokens, u.candidates_tokens, u.thoughts_tokens, u.tool_use_prompt_tokens) == (169, 20, 0, 0)
    assert u.latency_s == 1.235 and u.call == "context"
    assert llm._usage(SimpleNamespace(usage_metadata=None), "triage", 0).prompt_tokens == 0


def test_retry_only_on_429_and_503():
    def api_error(code):
        return errors.APIError(code, {"error": {"message": "x", "status": "X"}})
    assert llm._is_retryable(api_error(429)) and llm._is_retryable(api_error(503))
    assert not llm._is_retryable(api_error(400)) and not llm._is_retryable(ValueError())


def test_text_skips_thoughts_and_function_calls():
    content = types.Content(role="model", parts=[
        types.Part(text="hidden", thought=True),
        types.Part(function_call=types.FunctionCall(name="get_ticket", args={"ref": 1})),
        types.Part(text="Summary."),
    ])
    assert llm._text(content) == "Summary."
    assert llm._text(None) == ""


def test_add_tool_results_builds_one_user_turn():
    history = llm.new_history("request")
    calls = [llm.ToolCall("c1", "get_ticket", {"ref": 101}), llm.ToolCall("c2", "read_catalog", {"query": "x"})]
    llm.add_tool_results(history, [(calls[0], {"result": {"ref": 101}}), (calls[1], {"error": "e"})])
    turn = history.contents[-1]
    assert turn.role == "user" and len(turn.parts) == 2
    assert turn.parts[0].function_response.id == "c1"
    assert turn.parts[1].function_response.response == {"error": "e"}
