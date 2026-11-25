from __future__ import annotations

from typing import Any

import httpx
from decodo import AuthenticationError, DecodoError, RateLimitError, ValidationError
from decodo.errors import TimeoutError as DecodoTimeoutError

from .client import API_KEY_ENV_VAR

MAYBE_SENT_CODES = frozenset({"timeout", "network_error", "unexpected_error"})


def failure(message: str, code: str, *, retryable: bool = False, **details: Any) -> dict[str, Any]:
    return {"error": message, "code": code, "retryable": retryable, **details}


def failure_from(error: BaseException) -> dict[str, Any]:
    if isinstance(error, AuthenticationError):
        return failure(f"Decodo rejected the API key ({error}). Check {API_KEY_ENV_VAR}.", "authentication_failed")
    if isinstance(error, RateLimitError):
        return failure(f"Decodo rate limit reached: {error}", "rate_limited", retryable=True)
    if isinstance(error, ValidationError) or (isinstance(error, DecodoError) and error.status_code == 400):
        return failure(f"Decodo refused the input, change it before retrying: {error}", "invalid_input")
    if isinstance(error, DecodoTimeoutError):
        return failure(f"Decodo request timed out: {error}", "timeout", retryable=True)
    if isinstance(error, DecodoError):
        return failure(f"Decodo request failed: {error}", str(error.status_code), retryable=error.status_code >= 500)
    if isinstance(error, (httpx.LocalProtocolError, UnicodeEncodeError)):
        return failure(
            f"The request to Decodo could not be sent. Check {API_KEY_ENV_VAR} for invalid characters.",
            "invalid_request",
        )
    if isinstance(error, httpx.TransportError):
        return failure(f"Could not reach Decodo: {error}", "network_error", retryable=True)
    return failure(f"Decodo returned something unexpected ({type(error).__name__}).", "unexpected_error")


def maybe_sent(result: dict[str, Any], *, partly: bool = False) -> bool:
    code = str(result.get("code"))
    if partly and code == "invalid_input":
        return True
    return code in MAYBE_SENT_CODES or (code.isdigit() and int(code) >= 500)
