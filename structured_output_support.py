"""Strict JSON parsing and narrow provider compatibility policy for LLM output."""

from __future__ import annotations

import json
from urllib.parse import urlsplit


def parse_first_json_value(response: object) -> dict | list | None:
    """Return the first syntactically valid JSON object or array in a response.

    Prose and Markdown fences around a JSON value are tolerated. The JSON value
    itself is decoded by the standard library without attempting repairs.
    """
    if not isinstance(response, str) or not response:
        return None

    decoder = json.JSONDecoder()
    for index, character in enumerate(response):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(response, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value
    return None


def _http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _safe_provider_code(exc: BaseException) -> str:
    """Read only provider error-code fields; never return exception or body text."""
    candidates = [getattr(exc, "code", None), getattr(exc, "type", None)]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            candidates.extend((error.get("code"), error.get("type")))
    for candidate in candidates:
        if isinstance(candidate, str):
            value = candidate.strip().casefold().replace("-", "_").replace(" ", "_")
            if value:
                return value[:80]
    return ""


_SAFE_FINISH_REASONS = frozenset({
    "stop", "length", "content_filter", "insufficient_system_resource", "aborted",
})


def _safe_finish_reason(value):
    if not isinstance(value, str):
        return None
    return value if value in _SAFE_FINISH_REASONS else "unknown"


def classify_provider_exception(exc: BaseException) -> str:
    """Classify provider failures without retaining or exposing their raw message."""
    status = _http_status(exc)
    code = _safe_provider_code(exc)
    class_name = type(exc).__name__.casefold()
    safe_message = str(exc).casefold()

    if status in {401, 403} or any(token in code for token in ("invalid_api_key", "authentication", "unauthorized")) or "authentication" in class_name:
        return "authentication_failed"
    if status == 402 or any(token in code for token in ("insufficient_quota", "insufficient_balance", "billing_hard_limit", "payment_required")):
        return "insufficient_balance"
    if status == 404 or any(token in code for token in ("model_not_found", "model_not_available", "model_unavailable")):
        return "model_not_available"
    if status == 429 or "rate_limit" in class_name or "ratelimit" in class_name or "rate_limit" in code:
        return "rate_limited"
    if status is not None and 500 <= status <= 599:
        return "server_error"
    if status in {400, 422}:
        return "invalid_request"
    if isinstance(exc, TimeoutError) or "timeout" in class_name or "timed out" in safe_message:
        return "timeout"
    if isinstance(exc, ConnectionError) or any(token in class_name for token in ("connection", "connect", "network", "proxy")):
        return "connection_error"
    if isinstance(exc, ValueError) or any(token in safe_message for token in ("no llm configured", "no valid llm configuration", "invalid base_url", "invalid endpoint", "api key missing", "api 密钥缺失", "未配置任何 llm", "未找到有效的 llm 配置")):
        return "invalid_request"
    return "provider_error"


def structured_response_result(response, structured_mode="native") -> dict:
    """Extract only safe structured completion metadata from an SDK response."""
    safe_mode = structured_mode if isinstance(structured_mode, str) and structured_mode in {"native", "compatibility", "legacy"} else "unknown"
    choices = getattr(response, "choices", None)
    if not isinstance(choices, (list, tuple)) or not choices:
        return {
            "content": "",
            "finish_reason": None,
            "structured_mode": safe_mode,
            "http_status": _http_status(response) or 200,
            "error_code": "incomplete_response",
        }
    choice = choices[0]
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None)
    finish_reason = getattr(choice, "finish_reason", None)
    return {
        "content": content if isinstance(content, str) else "",
        "finish_reason": _safe_finish_reason(finish_reason),
        "structured_mode": safe_mode,
        "http_status": _http_status(response) or 200,
        "error_code": None,
    }


def structured_error_result(exc: BaseException, structured_mode="native", *, error_code=None) -> dict:
    """Create a credential-safe failure envelope; provider body/details are discarded."""
    safe_mode = structured_mode if isinstance(structured_mode, str) and structured_mode in {"native", "compatibility", "legacy"} else "unknown"
    safe_codes = {
        "authentication_failed", "insufficient_balance", "model_not_available",
        "rate_limited", "timeout", "connection_error", "server_error",
        "invalid_request", "provider_error", "incomplete_response",
    }
    safe_error = error_code if isinstance(error_code, str) and error_code in safe_codes else classify_provider_exception(exc)
    return {
        "content": "",
        "finish_reason": None,
        "structured_mode": safe_mode,
        "http_status": _http_status(exc),
        "error_code": safe_error,
    }


def is_response_format_unsupported(exc: BaseException) -> bool:
    """Allow compatibility retry only for an explicit 400/422 format rejection."""
    if _http_status(exc) not in {400, 422}:
        return False

    message = str(exc).casefold()
    body = getattr(exc, "body", None)
    if body is not None:
        message += " " + str(body).casefold()
    if not any(field in message for field in ("response_format", "json_object")):
        return False
    return any(
        marker in message
        for marker in (
            "unsupported",
            "not supported",
            "does not support",
            "invalid",
            "unrecognized",
            "unknown parameter",
            "not allowed",
            "unexpected parameter",
            "extra field",
        )
    )


def structured_request_options(base_url: str) -> dict:
    """Return JSON mode options, including DeepSeek's structured-call setting."""
    options = {"response_format": {"type": "json_object"}}
    try:
        hostname = (urlsplit(str(base_url or "").strip()).hostname or "").lower()
    except ValueError:
        hostname = ""
    if hostname == "api.deepseek.com":
        options["extra_body"] = {"thinking": {"type": "disabled"}}
    return options


def create_completion_with_json_fallback(create_completion, request_args: dict, *, mode_callback=None):
    """Make one native JSON request, falling back once only on explicit rejection."""
    if mode_callback is not None:
        mode_callback("native")
    try:
        return create_completion(**request_args), "native"
    except Exception as exc:
        if "response_format" not in request_args or not is_response_format_unsupported(exc):
            raise
        compatibility_args = dict(request_args)
        compatibility_args.pop("response_format", None)
        if mode_callback is not None:
            mode_callback("compatibility")
        return create_completion(**compatibility_args), "compatibility"
