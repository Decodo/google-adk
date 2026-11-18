"""Checks that DECODO_API_KEY is accepted: ``python -m decodo_adk.check``."""

from __future__ import annotations

import sys

import httpx
from decodo import AuthenticationError, RateLimitError, WebScrapingApi
from decodo.errors import TimeoutError as DecodoTimeoutError

from .client import API_KEY_ENV_VAR, build_api, load_schema, resolve_api_key


def api_key_problem(api: WebScrapingApi) -> str | None:
    """None when the key was accepted, otherwise why it could not be confirmed."""
    try:
        api.flows.check_key()
    except AuthenticationError as error:
        return f"Decodo rejected the API key ({error}). Check {API_KEY_ENV_VAR}."
    except RateLimitError:
        return "Decodo rate limit reached, so the key could not be checked. Try again shortly."
    except DecodoTimeoutError:
        return "The request to Decodo timed out, so the key could not be checked."
    except httpx.TransportError as error:
        return f"Could not reach Decodo ({type(error).__name__}), so the key could not be checked."
    except ValueError as error:
        return f"The key could not be checked: {error}"
    return None


def main() -> int:
    try:
        api = build_api(resolve_api_key(), load_schema())
    except ValueError as error:
        print(error)
        return 1
    problem = api_key_problem(api)
    print(problem or f"{API_KEY_ENV_VAR} is accepted.")
    return 1 if problem else 0


if __name__ == "__main__":
    sys.exit(main())
