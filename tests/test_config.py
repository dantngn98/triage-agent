import pytest
from pydantic import ValidationError

from triage import config
from triage.config import ROOT, Settings

ENV_VARS = ["GEMINI_API_KEY", "MODEL_ID", "THINKING_LEVEL", "MAX_CONTEXT_TURNS",
            "BOARD_PATH", "MAX_SPEND_USD", "AUTO_APPROVE"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_without_env_file():
    s = Settings(_env_file=None)
    assert s.model_id == "gemini-3.8-flash"
    assert s.thinking_level == "low"
    assert s.max_context_turns == 4
    assert s.board_path == ROOT / "data" / "board.json"
    assert s.max_spend_usd == 4.00
    assert s.auto_approve is False
    assert s.gemini_api_key.get_secret_value() == ""


def test_env_overrides_and_relative_board_path(monkeypatch):
    monkeypatch.setenv("THINKING_LEVEL", "medium")
    monkeypatch.setenv("BOARD_PATH", "data/other.json")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    s = Settings(_env_file=None)
    assert s.thinking_level == "medium"
    assert s.board_path == ROOT / "data" / "other.json"
    assert "secret-key" not in repr(s)


def test_unsupported_thinking_level_rejected(monkeypatch):
    monkeypatch.setenv("THINKING_LEVEL", "minimal")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_price_constants():
    assert config.INPUT_PRICE_PER_M == 0.75
    assert config.OUTPUT_PRICE_PER_M == 3.75
