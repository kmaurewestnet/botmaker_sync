from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime, timezone

import httpx

MAX_RETRIES = 3
BACKOFF_SECONDS = 1.0


def format_datetime(dt: datetime) -> str:
    """Botmaker's documented examples consistently use a literal 'Z' suffix
    (e.g. 2022-01-05T12:30:42Z); matching that exactly avoids relying on the
    API accepting '+00:00' offset notation too."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BotmakerClient:
    """Thin GET-only client for the Botmaker API. Follows `nextPage` for pagination
    -- it may come back as a full URL (chats/messages/sessions/agents/channels) or
    as an opaque token to resend via `next-page-token` (contacts); the spec is
    inconsistent about which, so both are handled."""

    def __init__(self, access_token: str, base_url: str, timeout: float = 60.0):
        self._client = httpx.Client(
            base_url=base_url,
            headers={"access-token": access_token},
            timeout=timeout,
        )

    def __enter__(self) -> "BotmakerClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str, params: dict | None) -> dict:
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            wait = BACKOFF_SECONDS * (2**attempt)
            try:
                resp = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                last_exc = exc
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = httpx.HTTPStatusError(
                        f"{resp.status_code} from {url}", request=resp.request, response=resp
                    )
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        wait = float(retry_after)
                else:
                    resp.raise_for_status()
                    return resp.json()
            if attempt < MAX_RETRIES:
                time.sleep(wait)
        assert last_exc is not None
        raise last_exc

    def get_pages(self, path: str, params: dict | None = None) -> Iterator[dict]:
        """Yield each page's parsed JSON body until `nextPage` is absent."""
        current_params = dict(params or {})
        next_url: str | None = None
        while True:
            if next_url is not None:
                data = self._get(next_url, params=None)
            else:
                data = self._get(path, params=current_params)
            yield data
            next_page = data.get("nextPage")
            if not next_page:
                return
            if next_page.startswith("http://") or next_page.startswith("https://"):
                next_url = next_page
            else:
                next_url = None
                current_params = {**current_params, "next-page-token": next_page}
