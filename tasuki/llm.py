"""LLM invocation (OpenAI API compatible + Cursor CLI). With fallback and retry."""

import json
import logging
import os
import subprocess
import time
from pathlib import Path

import yaml
from openai import OpenAI

log = logging.getLogger(__name__)

# --- Keywords for detecting rate limits ---
_RATE_LIMIT_KEYWORDS = [
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota exceeded",
    "capacity",
    "limit reached",
    "usage limit",
    "model limit",
    "429",
]


def _is_rate_limit_error(error: Exception | str) -> bool:
    """Determine whether an error indicates a rate limit or quota reached."""
    text = str(error).lower()
    return any(kw in text for kw in _RATE_LIMIT_KEYWORDS)


def load_config(config_path: Path | None = None) -> dict:
    from tasuki.config import load_config as _load
    return _load(config_path)


def get_provider(config: dict | None = None) -> str:
    """llm.provider: openai | cursor (defaults to cursor if not specified)."""
    cfg = config or load_config()
    return cfg.get("llm", {}).get("provider", "cursor")


def get_client(config: dict | None = None) -> OpenAI | None:
    """OpenAI client. Returns None when provider is cursor (CLI is called from chat)."""
    cfg = config or load_config()
    if get_provider(cfg) != "openai":
        return None
    llm = cfg.get("llm", {})
    api_key = llm.get("api_key") or os.environ.get("OPENAI_API_KEY")
    base_url = llm.get("base_url")
    kwargs = {}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(api_key=api_key, **kwargs)


def get_model(config: dict | None = None) -> str:
    cfg = config or load_config()
    return cfg.get("llm", {}).get("model", "gpt-5.2-codex-xhigh")


def get_fallback_models(config: dict | None = None) -> list[str]:
    """llm.fallback_models: list of alternative models when the main model reaches its limit."""
    cfg = config or load_config()
    fallbacks = cfg.get("llm", {}).get("fallback_models")
    if fallbacks and isinstance(fallbacks, list):
        return [str(m) for m in fallbacks]
    # Default fallback chain
    return ["opus-4.6-thinking", "auto"]


def get_retry_config(config: dict | None = None) -> dict:
    """llm.retry: retry configuration."""
    cfg = config or load_config()
    retry = cfg.get("llm", {}).get("retry", {})
    return {
        "max_retries": retry.get("max_retries", 3),
        "base_delay_sec": retry.get("base_delay_sec", 2),
        "max_delay_sec": retry.get("max_delay_sec", 60),
    }


def _resolve_agent_cli(configured_path: str) -> str:
    """Resolve the Cursor CLI (agent) path. Falls back to common install locations."""
    import shutil

    found = shutil.which(configured_path)
    if found:
        return found
    # Check common installation paths
    common_paths = [
        Path.home() / ".local" / "bin" / "agent",
        Path("/usr/local/bin/agent"),
        Path.home() / ".cursor" / "bin" / "agent",
    ]
    for p in common_paths:
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return configured_path  # Return as-is; will raise FileNotFoundError later


def _chat_cursor_cli(
    model: str,
    system: str,
    user: str,
    config: dict,
) -> str:
    """Single chat via Cursor CLI (agent -p). Uses CURSOR_API_KEY or an authenticated session."""
    llm = config.get("llm", {})
    cli_path = _resolve_agent_cli(
        llm.get("cursor_cli_path") or os.environ.get("CURSOR_AGENT_PATH", "agent")
    )
    timeout = llm.get("cursor_timeout_sec") or 600
    # Combine into a single prompt (CLI cannot pass system separately)
    prompt = f"{system}\n\n---\n\n{user}"
    cmd = [
        cli_path,
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
        prompt,
    ]
    env = os.environ.copy()
    if llm.get("api_key"):
        env["CURSOR_API_KEY"] = str(llm["api_key"])
    cwd = None  # Use the process's current directory
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"Cursor CLI not found: {cli_path}. "
            "Install: curl https://cursor.com/install -fsS | bash or set llm.cursor_cli_path"
        ) from None
    if out.returncode != 0:
        raise RuntimeError(
            f"Cursor CLI exited with code {out.returncode}. stderr: {out.stderr[:1000] if out.stderr else 'none'}"
        )
    # Find the last JSON line with (type=result, result=...)
    result_text = ""
    for line in out.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get("type") == "result" and "result" in obj:
                result_text = obj.get("result", "") or ""
                break
        except json.JSONDecodeError:
            continue
    return result_text


def _chat_openai(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    messages: list[dict] | None = None,
) -> str:
    """Single chat via OpenAI API compatible endpoint."""
    msgs = [{"role": "system", "content": system}]
    if messages:
        msgs.extend(messages)
    msgs.append({"role": "user", "content": user})
    r = client.chat.completions.create(model=model, messages=msgs)
    return r.choices[0].message.content or ""


def _call_once(
    provider: str,
    client: OpenAI | None,
    model: str,
    system: str,
    user: str,
    messages: list[dict] | None,
    config: dict,
) -> str:
    """Single call with the specified provider and model. Exceptions are raised as-is."""
    if provider == "cursor":
        return _chat_cursor_cli(model, system, user, config)
    if client is None:
        client = get_client(config)
    return _chat_openai(client, model, system, user, messages)


def chat(
    client: OpenAI | None,
    model: str,
    system: str,
    user: str,
    messages: list[dict] | None = None,
    config: dict | None = None,
) -> str:
    """Return a single chat completion. On rate limit, retry then auto-switch to fallback models."""
    cfg = config or load_config()
    provider = get_provider(cfg)
    retry_cfg = get_retry_config(cfg)
    max_retries = retry_cfg["max_retries"]
    base_delay = retry_cfg["base_delay_sec"]
    max_delay = retry_cfg["max_delay_sec"]

    # Main model + fallback chain
    fallbacks = get_fallback_models(cfg)
    models_to_try = [model] + [m for m in fallbacks if m != model]

    last_error: Exception | None = None

    for model_name in models_to_try:
        for attempt in range(max_retries):
            try:
                result = _call_once(provider, client, model_name, system, user, messages, cfg)
                if model_name != model:
                    log.warning("Succeeded with fallback model %s (original: %s)", model_name, model)
                return result
            except Exception as e:
                last_error = e
                if _is_rate_limit_error(e):
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    log.warning(
                        "Rate limit: model=%s, attempt=%d/%d, retrying in %.1fs: %s",
                        model_name, attempt + 1, max_retries, delay, e,
                    )
                    time.sleep(delay)
                else:
                    # Non-rate-limit error -> skip retry and try the next model
                    log.warning("Error (model=%s): %s — trying next model", model_name, e)
                    break
        else:
            # All max_retries attempts hit rate limit -> move to next model
            log.warning(
                "model=%s still rate-limited after %d retries. Moving to next fallback model", model_name, max_retries
            )
            continue

    # All models failed
    raise RuntimeError(
        f"All models {models_to_try} failed. Last error: {last_error}"
    )
