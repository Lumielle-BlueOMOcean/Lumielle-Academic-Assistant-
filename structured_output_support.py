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


def create_completion_with_json_fallback(create_completion, request_args: dict):
    """Make one native JSON request, falling back once only on explicit rejection."""
    try:
        return create_completion(**request_args), "native"
    except Exception as exc:
        if "response_format" not in request_args or not is_response_format_unsupported(exc):
            raise
        compatibility_args = dict(request_args)
        compatibility_args.pop("response_format", None)
        return create_completion(**compatibility_args), "compatibility"
