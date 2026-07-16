import http.client
import logging
import urllib.request
from urllib.error import HTTPError, URLError

import pytest

from http_retry.fetch import DEFAULT_MAX_RETRIES, fetch_with_retry


class _FakeResponse:
    def __init__(self, body: bytes = b"ok") -> None:
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _consume_bytes(response: _FakeResponse) -> bytes:
    return response.read()


def test_returns_consumed_value_on_first_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", lambda request: _FakeResponse(b"hello"))
    assert fetch_with_retry("https://example.com", _consume_bytes) == b"hello"


def test_rate_limited_request_retries_after_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []
    sleeps = []

    def fake_urlopen(request: str) -> _FakeResponse:
        attempts.append(request)
        if len(attempts) == 1:
            raise HTTPError(request, 429, "Too Many Requests", {"Retry-After": "5"}, None)  # type: ignore[arg-type]
        return _FakeResponse(b"hello")

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("http_retry.fetch.time.sleep", lambda seconds: sleeps.append(seconds))

    assert fetch_with_retry("https://example.com", _consume_bytes) == b"hello"
    assert sleeps == [5]
    assert len(attempts) == 2


def test_rate_limited_request_falls_back_to_default_delay_without_retry_after_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = []
    sleeps = []

    def fake_urlopen(request: str) -> _FakeResponse:
        attempts.append(request)
        if len(attempts) == 1:
            raise HTTPError(request, 429, "Too Many Requests", None, None)  # type: ignore[arg-type]
        return _FakeResponse(b"hello")

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("http_retry.fetch.time.sleep", lambda seconds: sleeps.append(seconds))

    fetch_with_retry("https://example.com", _consume_bytes, retry_after_default=30)
    assert sleeps == [30]


def test_incomplete_read_retries_after_a_short_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []
    sleeps = []

    def fake_urlopen(request: str) -> _FakeResponse:
        attempts.append(request)
        if len(attempts) == 1:
            raise http.client.IncompleteRead(b"partial", 100)
        return _FakeResponse(b"hello")

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("http_retry.fetch.time.sleep", lambda seconds: sleeps.append(seconds))

    assert fetch_with_retry("https://example.com", _consume_bytes) == b"hello"
    assert len(sleeps) == 1
    assert len(attempts) == 2


def test_url_error_is_retried_as_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    # This is the shape of failure seen in the real sync.yml run: a bare connection timeout
    # (errno 110) surfaces as URLError, not as an HTTP status.
    attempts = []

    def fake_urlopen(request: str) -> _FakeResponse:
        attempts.append(request)
        if len(attempts) == 1:
            raise URLError(TimeoutError(110, "Connection timed out"))
        return _FakeResponse(b"hello")

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("http_retry.fetch.time.sleep", lambda seconds: None)

    assert fetch_with_retry("https://example.com", _consume_bytes) == b"hello"
    assert len(attempts) == 2


def test_remote_disconnected_is_retried_as_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def fake_urlopen(request: str) -> _FakeResponse:
        attempts.append(request)
        if len(attempts) == 1:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        return _FakeResponse(b"hello")

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("http_retry.fetch.time.sleep", lambda seconds: None)

    assert fetch_with_retry("https://example.com", _consume_bytes) == b"hello"
    assert len(attempts) == 2


def test_transient_network_error_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def fake_urlopen(request: str) -> _FakeResponse:
        attempts.append(request)
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("http_retry.fetch.time.sleep", lambda seconds: None)

    with pytest.raises(http.client.RemoteDisconnected):
        fetch_with_retry("https://example.com", _consume_bytes)

    assert len(attempts) == DEFAULT_MAX_RETRIES


def test_max_retries_is_configurable_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def fake_urlopen(request: str) -> _FakeResponse:
        attempts.append(request)
        raise URLError("boom")

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("http_retry.fetch.time.sleep", lambda seconds: None)

    with pytest.raises(URLError):
        fetch_with_retry("https://example.com", _consume_bytes, max_retries=2)

    assert len(attempts) == 2


def test_non_rate_limit_http_error_is_not_retried(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    attempts = []

    def fake_urlopen(request: str) -> _FakeResponse:
        attempts.append(request)
        raise HTTPError(request, 404, "Not Found", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)

    with caplog.at_level(logging.ERROR, logger="http_retry.fetch"):
        with pytest.raises(HTTPError):
            fetch_with_retry("https://example.com", _consume_bytes)

    assert len(attempts) == 1
    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_describe_is_used_in_logs_instead_of_the_raw_request(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        raise HTTPError(request.full_url, 404, "Not Found", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)
    request = urllib.request.Request("https://example.com/secret?api_key=shh")

    with caplog.at_level(logging.ERROR, logger="http_retry.fetch"):
        with pytest.raises(HTTPError):
            fetch_with_retry(request, _consume_bytes, describe="https://example.com/secret?api_key=***")

    assert not any("shh" in record.getMessage() for record in caplog.records)
    assert any("***" in record.getMessage() for record in caplog.records)


def test_consume_is_reinvoked_on_each_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    # A failure like IncompleteRead happens *while* consume() reads the body, not while opening
    # the connection -- so a fresh attempt must call consume() again, not reuse a stale result.
    responses = [_FakeResponse(b"partial"), _FakeResponse(b"complete")]
    calls = []

    def consume(response: _FakeResponse) -> bytes:
        calls.append(response)
        if len(calls) == 1:
            raise http.client.IncompleteRead(b"partial", 100)
        return response.read()

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", lambda request: responses.pop(0))
    monkeypatch.setattr("http_retry.fetch.time.sleep", lambda seconds: None)

    assert fetch_with_retry("https://example.com", consume) == b"complete"
    assert len(calls) == 2
