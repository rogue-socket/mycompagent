"""Configuration management for the DOM-driven browser agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # noqa: BLE001
    yaml = None


DEFAULT_CONFIG_PATH = "~/.browser_agent/config.yaml"
DEFAULT_MODEL = "gemini-1.5-flash"
DEFAULT_MODE = "safe"
DEFAULT_MAX_STEPS = 50
DEFAULT_MAX_ERRORS = 5
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_ELEMENTS = 60
DEFAULT_MAX_VISIBLE_CHARS = 2000
DEFAULT_MIN_VISIBLE_TEXT = 200
DEFAULT_SESSION = ""
DEFAULT_START_URL = ""
DEFAULT_USE_NPX = False


class ConfigError(RuntimeError):
    """Raised when configuration is invalid."""


class ConfigManager:
    """Read, write, and initialize configuration."""

    def __init__(self, config_path: str | None = None) -> None:
        self.config_path = Path(config_path or DEFAULT_CONFIG_PATH).expanduser()

    @staticmethod
    def defaults() -> dict[str, Any]:
        return {
            "api_key": "",
            "model": DEFAULT_MODEL,
            "mode": DEFAULT_MODE,
            "max_steps": DEFAULT_MAX_STEPS,
            "max_errors": DEFAULT_MAX_ERRORS,
            "max_retries": DEFAULT_MAX_RETRIES,
            "max_elements": DEFAULT_MAX_ELEMENTS,
            "max_visible_chars": DEFAULT_MAX_VISIBLE_CHARS,
            "min_visible_text": DEFAULT_MIN_VISIBLE_TEXT,
            "session": DEFAULT_SESSION,
            "start_url": DEFAULT_START_URL,
            "use_npx": DEFAULT_USE_NPX,
        }

    def exists(self) -> bool:
        return self.config_path.exists()

    def load(self) -> dict[str, Any]:
        if not self.exists():
            return self.first_run_setup()
        data = _safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        merged = {**self.defaults(), **data}
        self.validate(merged)
        return merged

    def save(self, config: dict[str, Any]) -> None:
        self.validate(config)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(_safe_dump(config), encoding="utf-8")

    def first_run_setup(self) -> dict[str, Any]:
        print("No browser agent config found. Starting setup...")
        api_key = input("LLM API key (Gemini): ").strip()
        model = input(f"Model [{DEFAULT_MODEL}]: ").strip() or DEFAULT_MODEL
        mode = input(f"Default mode [{DEFAULT_MODE}]: ").strip() or DEFAULT_MODE
        session = input("Default Playwright session name (optional): ").strip()
        start_url = input("Default start URL (optional): ").strip()
        use_npx_raw = input("Use npx playwright-cli by default? [y/N]: ").strip().lower()

        config = {
            **self.defaults(),
            "api_key": api_key,
            "model": model,
            "mode": mode,
            "session": session,
            "start_url": start_url,
            "use_npx": use_npx_raw in {"y", "yes"},
        }
        self.save(config)
        print(f"Saved config to {self.config_path}")
        return config

    def validate(self, config: dict[str, Any]) -> None:
        if not str(config.get("api_key", "")).strip():
            raise ConfigError("Config value 'api_key' must be non-empty")
        if not str(config.get("model", "")).strip():
            raise ConfigError("Config value 'model' must be non-empty")
        if str(config.get("mode", "")) not in {"safe", "hybrid", "auto"}:
            raise ConfigError("Config value 'mode' must be safe, hybrid, or auto")
        for key in (
            "max_steps",
            "max_errors",
            "max_retries",
            "max_elements",
            "max_visible_chars",
            "min_visible_text",
        ):
            try:
                value = int(config.get(key, 0))
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"Config value '{key}' must be numeric") from exc
            if value <= 0:
                raise ConfigError(f"Config value '{key}' must be positive")


    def merge_overrides(
        self,
        config: dict[str, Any],
        *,
        model: str | None,
        mode: str | None,
        max_steps: int | None,
    ) -> dict[str, Any]:
        merged = dict(config)
        if model:
            merged["model"] = model
        if mode:
            merged["mode"] = mode
        if max_steps is not None:
            merged["max_steps"] = max_steps
        self.validate(merged)
        return merged


def _safe_load(text: str) -> dict[str, Any]:
    if yaml is not None:
        return yaml.safe_load(text) or {}
    return json.loads(text) if text.strip() else {}


def _safe_dump(data: dict[str, Any]) -> str:
    if yaml is not None:
        return yaml.safe_dump(data, sort_keys=False)
    return json.dumps(data, indent=2)
