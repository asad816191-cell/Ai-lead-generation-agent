"""Small JSON HTTP client with retries, used by all API providers."""
from __future__ import annotations

import time

import requests


class HttpError(Exception):
    """Raised for failed API calls. `status` is None for network-level problems."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _extract_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return (response.text or "")[:200] or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
        if isinstance(err, str):
            return err[:300]
        detail = body.get("detail")
        if isinstance(detail, dict) and detail.get("error"):
            return str(detail["error"])[:300]
        if isinstance(detail, str):
            return detail[:300]
        if body.get("message"):
            return str(body["message"])[:300]
    return f"HTTP {response.status_code}"


def request_json(method: str, url: str, *, headers=None, json_body=None, params=None,
                 timeout: int = 15, attempts: int = 3) -> dict:
    last: HttpError | None = None
    for attempt in range(attempts):
        try:
            response = requests.request(method, url, headers=headers, json=json_body,
                                        params=params, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last = HttpError(f"network problem ({exc.__class__.__name__})", None)
            time.sleep(min(2 ** attempt, 5))
            continue
        except requests.RequestException as exc:
            raise HttpError(f"request failed ({exc.__class__.__name__})", None) from exc

        if response.status_code == 429 or response.status_code >= 500:
            label = "rate limit reached" if response.status_code == 429 else "server error"
            last = HttpError(f"{label} (HTTP {response.status_code}): {_extract_message(response)}",
                             response.status_code)
            try:
                delay = min(float(response.headers.get("Retry-After", 0)), 10)
            except ValueError:
                delay = 0
            time.sleep(delay or min(2 ** attempt, 5))
            continue
        if response.status_code >= 400:
            raise HttpError(f"HTTP {response.status_code}: {_extract_message(response)}",
                            response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise HttpError("provider returned invalid JSON", response.status_code) from exc
    raise last or HttpError("request failed")
