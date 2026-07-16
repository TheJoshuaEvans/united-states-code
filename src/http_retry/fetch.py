"""Single retry-with-backoff policy for every `urlopen` call this repo makes.

Both mirrors (`uscode_mirror`, `congress_bills_mirror`) talk to flaky-ish government hosts over
plain HTTP -- dropped connections, brief outages, occasional rate limiting. `fetch_with_retry` is
the one place that policy lives, so callers just describe *what* to fetch and *how* to consume the
response; retrying HTTP 429 and transient connection failures is handled the same way everywhere.
"""

from __future__ import annotations

import http.client
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_AFTER_SECONDS = 60
DEFAULT_TRANSIENT_RETRY_DELAY_SECONDS = 5

# Connection-level failures (the server hung up, the connection dropped mid-response, the initial
# TCP connect itself timed out) rather than an actual HTTP error response. Distinct from
# `urllib.error.HTTPError` (a real response, just an error status), which is handled separately and
# only retried for 429. `URLError` is `HTTPError`'s own base class, but that's harmless here: an
# `except HTTPError` clause earlier in the chain always claims an HTTPError instance first,
# regardless of a broader `URLError` clause appearing later.
TRANSIENT_EXCEPTIONS = (
    http.client.IncompleteRead,
    http.client.RemoteDisconnected,
    ConnectionError,
    TimeoutError,
    urllib.error.URLError,
)


def fetch_with_retry[T](
    request: urllib.request.Request | str,
    consume: Callable[[http.client.HTTPResponse], T],
    *,
    describe: str | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_after_default: int = DEFAULT_RETRY_AFTER_SECONDS,
    transient_retry_delay: int = DEFAULT_TRANSIENT_RETRY_DELAY_SECONDS,
) -> T:
    """Open `request` and hand the response to `consume`, retrying on transient failure.

    Retries HTTP 429 (honoring a `Retry-After` header when the server sends one) and transient
    connection-level failures (dropped/incomplete responses, timed-out connects) after a short
    fixed delay. Any other HTTP error is raised immediately, not retried. `consume` is re-invoked
    on every attempt, inside the `with urlopen(...)` block, since failures like `IncompleteRead`
    happen while reading the body, not just while opening the connection.

    `describe` is what gets logged in place of the URL -- pass a redacted string when `request`'s
    URL carries a secret (e.g. an API key) that shouldn't end up in logs.
    """
    label = describe if describe is not None else (request.full_url if isinstance(request, urllib.request.Request) else request)

    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(request) as response:
                return consume(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                retry_after = retry_after_default
                if exc.headers and exc.headers.get("Retry-After"):
                    retry_after = int(exc.headers["Retry-After"])
                logger.warning(
                    "Rate limited fetching %s -- sleeping %ss (attempt %d/%d)",
                    label, retry_after, attempt, max_retries,
                )
                time.sleep(retry_after)
                continue
            logger.error("Failed fetching %s: HTTP %s", label, exc.code)
            raise
        except TRANSIENT_EXCEPTIONS as exc:
            if attempt < max_retries:
                logger.warning(
                    "Transient network error fetching %s (%s: %s) -- retrying in %ss (attempt %d/%d)",
                    label, type(exc).__name__, exc, transient_retry_delay, attempt, max_retries,
                )
                time.sleep(transient_retry_delay)
                continue
            logger.error("Failed fetching %s after %d attempts: %s", label, attempt, exc)
            raise
