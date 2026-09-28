"""Shared helpers for testing OpenAI-compatible model connections."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit, urlunsplit

import openai
import requests
from openai import OpenAI


def default_deepseek_profile(name: str) -> dict[str, str]:
    """Return the default profile used only when a user's profile file is absent."""
    return {
        "id": "default",
        "name": name,
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-flash",
    }


def make_connection_signature(base_url: str, api_key: str, model_name: str) -> str:
    """Hash the tested configuration without storing its API key as plain text."""
    values = [str(base_url or "").strip(), str(api_key or ""), str(model_name or "").strip()]
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _models_url(base_url: str) -> str:
    parsed = urlsplit(base_url.strip())
    path = parsed.path.rstrip("/")
    if not path.endswith("/models"):
        path = f"{path}/models" if path else "/models"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _code_for_http_status(status_code: int | None) -> str:
    return {
        401: "authentication_failed",
        402: "insufficient_balance",
        404: "model_not_available",
        408: "timeout",
        429: "rate_limited",
    }.get(status_code, "provider_error" if status_code is not None else "unknown_error")


def _safe_message(code: str) -> str:
    return {
        "missing_api_key": "Enter an API key to test this model.",
        "missing_base_url": "Enter the provider Base URL to test this model.",
        "missing_model": "Select a model before testing the connection.",
        "authentication_failed": "The API key was rejected. Check that you copied the complete key.",
        "insufficient_balance": "The provider account has insufficient balance or credit.",
        "model_not_available": "The model is unavailable. Fetch the model list and select an available model.",
        "rate_limited": "The provider is rate limiting requests. Wait a moment and try again.",
        "timeout": "The request timed out. Check your network and try again.",
        "connection_error": "Could not reach the provider. Check the network and Base URL.",
        "provider_error": "The provider could not complete this request. Check the Base URL and provider service status.",
        "unknown_error": "The connection test could not be completed.",
    }.get(code, "The connection test could not be completed.")


def _failure(code: str, model_name: str = "", http_status: int | None = None) -> dict:
    result = {
        "ok": False,
        "code": code,
        "model": model_name,
        "message": _safe_message(code),
    }
    if http_status is not None:
        result["http_status"] = http_status
    return result


def _exception_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _classify_exception(exc: Exception) -> tuple[str, int | None]:
    status = _exception_status(exc)
    if status is not None:
        return _code_for_http_status(status), status
    if isinstance(exc, (openai.APITimeoutError, requests.exceptions.Timeout, TimeoutError)):
        return "timeout", None
    if isinstance(exc, (openai.APIConnectionError, requests.exceptions.ConnectionError, ConnectionError)):
        return "connection_error", None
    return "unknown_error", None


def fetch_available_models(base_url: str, api_key: str, timeout: int = 15) -> dict:
    """Fetch model IDs from a provider's OpenAI-compatible ``/models`` endpoint."""
    if not str(base_url or "").strip():
        return _failure("missing_base_url")
    headers = {"Authorization": f"Bearer {api_key.strip()}"} if str(api_key or "").strip() else {}
    try:
        url = _models_url(base_url)
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code != 200:
            code = _code_for_http_status(response.status_code)
            if response.status_code == 404:
                code = "provider_error"
            return _failure(code, http_status=response.status_code)
        payload = response.json()
        models = payload.get("data", []) if isinstance(payload, dict) else []
        names = []
        for model in models:
            model_id = model.get("id") if isinstance(model, dict) else str(model)
            if model_id:
                names.append(str(model_id))
        if not names:
            return _failure("provider_error")
        return {"ok": True, "code": "ok", "models": sorted(set(names)), "message": ""}
    except (requests.exceptions.Timeout, TimeoutError):
        return _failure("timeout")
    except requests.exceptions.ConnectionError:
        return _failure("connection_error")
    except Exception:
        # Provider and transport exception text may contain credentials; never return it.
        return _failure("unknown_error")


def _has_valid_completion(response) -> bool:
    choices = getattr(response, "choices", None)
    return bool(choices) and any(getattr(choice, "message", None) is not None for choice in choices)


def test_model_connection(
    base_url: str,
    api_key: str,
    model_name: str,
    timeout: int = 20,
) -> dict:
    """Send one tiny chat-completions request and return a credential-safe result."""
    normalized_base_url = str(base_url or "").strip()
    normalized_key = str(api_key or "").strip()
    normalized_model = str(model_name or "").strip()
    if not normalized_base_url:
        return _failure("missing_base_url", normalized_model)
    if not normalized_key:
        return _failure("missing_api_key", normalized_model)
    if not normalized_model:
        return _failure("missing_model")

    request_args = {
        "model": normalized_model,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "temperature": 0,
        "max_tokens": 8,
    }
    try:
        deepseek_host = (urlsplit(normalized_base_url).hostname or "").lower() == "api.deepseek.com"
    except ValueError:
        deepseek_host = False
    if deepseek_host:
        request_args["extra_body"] = {"thinking": {"type": "disabled"}}

    try:
        client = OpenAI(
            api_key=normalized_key,
            base_url=normalized_base_url,
            timeout=timeout,
            max_retries=0,
        )
        response = client.chat.completions.create(**request_args)
        if not _has_valid_completion(response):
            return _failure("provider_error", normalized_model)
        return {
            "ok": True,
            "code": "ok",
            "model": normalized_model,
            "http_status": 200,
            "message": "",
        }
    except Exception as exc:
        code, status = _classify_exception(exc)
        return _failure(code, normalized_model, status)
