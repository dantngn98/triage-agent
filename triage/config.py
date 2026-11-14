"""Settings from .env via pydantic-settings, plus price constants."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]

# Gemini 3.8 Flash introductory pricing through year end, USD per 1M tokens.
# Thinking tokens are billed as output.
INPUT_PRICE_PER_M = 0.75
OUTPUT_PRICE_PER_M = 3.75


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    gemini_api_key: SecretStr = SecretStr("")
    model_id: str = "gemini-3.8-flash"
    # 3.8 Flash supports low/medium/high; minimal is not supported.
    thinking_level: Literal["low", "medium", "high"] = "low"
    max_context_turns: int = Field(default=4, ge=1)
    board_path: Path = ROOT / "data" / "board.json"
    seed_board_path: Path = ROOT / "data" / "seed_board.json"
    drafts_dir: Path = ROOT / "drafts"
    max_spend_usd: float = Field(default=4.00, gt=0)
    auto_approve: bool = False
    # Client-side pacing: max model calls per minute, 0 disables. Set from AI Studio limits.
    rpm_limit: int = Field(default=0, ge=0)

    @field_validator("board_path", "seed_board_path", "drafts_dir")
    @classmethod
    def _resolve_against_root(cls, v: Path) -> Path:
        return v if v.is_absolute() else ROOT / v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
