import http.client
import json
import logging
import urllib.request
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from congress_bills_mirror.client import (
    _MAX_RETRIES,
    MissingApiKeyError,
    get_bill_detail,
    get_committees,
    get_cosponsors,
    get_current_congress,
    get_summaries,
    get_text_versions,
    iter_bill_summaries,
    iter_pages,
)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONGRESS_API_KEY", "test-key")


class _FakeResponse:
    def __init__(self, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self.headers = headers or {}

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _url_of(request: urllib.request.Request) -> str:
    return request.full_url


def test_missing_api_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONGRESS_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError):
        get_current_congress()


def test_get_current_congress_parses_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "congress_bills_mirror.client.urllib.request.urlopen",
        lambda request: _FakeResponse({"congress": {"number": 119, "name": "119th Congress"}}),
    )
    assert get_current_congress() == 119


def test_get_bill_detail_returns_the_bill_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "congress_bills_mirror.client.urllib.request.urlopen",
        lambda request: _FakeResponse({"bill": {"congress": 119, "type": "HR", "number": "877"}}),
    )
    assert get_bill_detail(119, "hr", "877") == {"congress": 119, "type": "HR", "number": "877"}


def test_requests_identify_themselves_with_a_custom_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = []

    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        requests.append(request)
        return _FakeResponse({"congress": {"number": 119}})

    monkeypatch.setattr("congress_bills_mirror.client.urllib.request.urlopen", fake_urlopen)
    get_current_congress()

    assert requests[0].get_header("User-agent") is not None
    assert "python-urllib" not in requests[0].get_header("User-agent").lower()


def test_api_key_is_appended_to_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_urls = []

    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        requested_urls.append(_url_of(request))
        return _FakeResponse({"congress": {"number": 119}})

    monkeypatch.setattr("congress_bills_mirror.client.urllib.request.urlopen", fake_urlopen)
    get_current_congress()

    assert "api_key=test-key" in requested_urls[0]


def test_iter_pages_follows_pagination_next_until_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {"bills": [{"number": "1"}], "pagination": {"next": "https://api.congress.gov/v3/bill/119?offset=1&limit=1"}},
        {"bills": [{"number": "2"}], "pagination": {}},
    ]
    requested_urls = []

    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        requested_urls.append(_url_of(request))
        return _FakeResponse(pages[len(requested_urls) - 1])

    monkeypatch.setattr("congress_bills_mirror.client.urllib.request.urlopen", fake_urlopen)

    collected = list(iter_pages("/bill/119"))

    assert len(collected) == 2
    assert collected[0]["bills"] == [{"number": "1"}]
    assert collected[1]["bills"] == [{"number": "2"}]
    assert len(requested_urls) == 2


def test_iter_pages_encodes_a_literal_space_in_the_next_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # Confirmed live: congress.gov's own `pagination.next` links come back with an unencoded
    # space (e.g. "sort=updateDate asc"), which crashes Python's http.client if passed through as-is.
    pages = [
        {
            "bills": [],
            "pagination": {"next": "https://api.congress.gov/v3/bill/119?sort=updateDate asc&offset=250&limit=250"},
        },
        {"bills": [], "pagination": {}},
    ]
    requested_urls = []

    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        requested_urls.append(_url_of(request))
        return _FakeResponse(pages[len(requested_urls) - 1])

    monkeypatch.setattr("congress_bills_mirror.client.urllib.request.urlopen", fake_urlopen)
    list(iter_pages("/bill/119"))

    assert " " not in requested_urls[1]
    assert "sort=updateDate%20asc" in requested_urls[1]


def test_iter_pages_next_url_gets_api_key_appended(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {"bills": [], "pagination": {"next": "https://api.congress.gov/v3/bill/119?offset=250&limit=250&format=json"}},
        {"bills": [], "pagination": {}},
    ]
    requested_urls = []

    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        requested_urls.append(_url_of(request))
        return _FakeResponse(pages[len(requested_urls) - 1])

    monkeypatch.setattr("congress_bills_mirror.client.urllib.request.urlopen", fake_urlopen)
    list(iter_pages("/bill/119"))

    assert "api_key=test-key" in requested_urls[1]


def test_iter_pages_handles_a_null_pagination_field_without_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "congress_bills_mirror.client.urllib.request.urlopen",
        lambda request: _FakeResponse({"bills": [{"number": "1"}], "pagination": None}),
    )

    collected = list(iter_pages("/bill/119"))

    assert len(collected) == 1


def test_iter_bill_summaries_handles_a_null_bills_field_without_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "congress_bills_mirror.client.urllib.request.urlopen",
        lambda request: _FakeResponse({"bills": None, "pagination": {}}),
    )

    assert list(iter_bill_summaries(119, "2026-07-11T00:00:00Z")) == []


def test_iter_bill_summaries_flattens_bills_across_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {"bills": [{"number": "1"}], "pagination": {"next": "https://api.congress.gov/v3/bill/119?offset=1"}},
        {"bills": [{"number": "2"}], "pagination": {}},
    ]
    requested_urls = []

    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        requested_urls.append(_url_of(request))
        return _FakeResponse(pages[len(requested_urls) - 1])

    monkeypatch.setattr("congress_bills_mirror.client.urllib.request.urlopen", fake_urlopen)

    numbers = [b["number"] for b in iter_bill_summaries(119, "2026-07-11T00:00:00Z")]
    assert numbers == ["1", "2"]
    assert "fromDateTime=2026-07-11" in requested_urls[0]


@pytest.mark.parametrize(
    ("fetch", "key"),
    [
        (get_cosponsors, "cosponsors"),
        (get_committees, "committees"),
        (get_summaries, "summaries"),
        (get_text_versions, "textVersions"),
    ],
)
def test_bill_subresource_getters_extract_their_own_key(
    monkeypatch: pytest.MonkeyPatch, fetch: Any, key: str
) -> None:
    monkeypatch.setattr(
        "congress_bills_mirror.client.urllib.request.urlopen",
        lambda request: _FakeResponse({key: [{"id": "a"}], "pagination": {}}),
    )
    assert fetch(119, "hr", "877") == [{"id": "a"}]


def test_bill_subresource_getters_return_empty_list_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "congress_bills_mirror.client.urllib.request.urlopen",
        lambda request: _FakeResponse({"pagination": {}}),
    )
    assert get_cosponsors(119, "hr", "877") == []


def test_bill_subresource_getters_return_empty_list_when_key_is_explicitly_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "congress_bills_mirror.client.urllib.request.urlopen",
        lambda request: _FakeResponse({"cosponsors": None, "pagination": {}}),
    )
    assert get_cosponsors(119, "hr", "877") == []


def test_rate_limited_request_retries_after_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []
    sleeps = []

    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        attempts.append(request)
        if len(attempts) == 1:
            raise HTTPError(_url_of(request), 429, "Too Many Requests", {"Retry-After": "5"}, None)  # type: ignore[arg-type]
        return _FakeResponse({"congress": {"number": 119}})

    monkeypatch.setattr("congress_bills_mirror.client.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("congress_bills_mirror.client.time.sleep", lambda seconds: sleeps.append(seconds))

    assert get_current_congress() == 119
    assert sleeps == [5]
    assert len(attempts) == 2


def test_incomplete_read_retries_after_a_short_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    # Confirmed live: congress.gov's connection dropped mid-response during a real sync run.
    attempts = []
    sleeps = []

    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        attempts.append(request)
        if len(attempts) == 1:
            raise http.client.IncompleteRead(b"partial", 100)
        return _FakeResponse({"congress": {"number": 119}})

    monkeypatch.setattr("congress_bills_mirror.client.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("congress_bills_mirror.client.time.sleep", lambda seconds: sleeps.append(seconds))

    assert get_current_congress() == 119
    assert len(sleeps) == 1
    assert len(attempts) == 2


def test_url_error_is_retried_as_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        attempts.append(request)
        if len(attempts) == 1:
            raise URLError("Connection refused")
        return _FakeResponse({"congress": {"number": 119}})

    monkeypatch.setattr("congress_bills_mirror.client.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("congress_bills_mirror.client.time.sleep", lambda seconds: None)

    assert get_current_congress() == 119
    assert len(attempts) == 2


def test_transient_network_error_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        attempts.append(request)
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr("congress_bills_mirror.client.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("congress_bills_mirror.client.time.sleep", lambda seconds: None)

    with pytest.raises(http.client.RemoteDisconnected):
        get_current_congress()

    assert len(attempts) == _MAX_RETRIES


def test_non_rate_limit_http_error_is_not_retried(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        raise HTTPError(_url_of(request), 404, "Not Found", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr("congress_bills_mirror.client.urllib.request.urlopen", fake_urlopen)

    with caplog.at_level(logging.ERROR, logger="congress_bills_mirror.client"):
        with pytest.raises(HTTPError):
            get_current_congress()

    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_api_key_never_appears_in_logs_on_error(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        raise HTTPError(_url_of(request), 404, "Not Found", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr("congress_bills_mirror.client.urllib.request.urlopen", fake_urlopen)

    with caplog.at_level(logging.ERROR, logger="congress_bills_mirror.client"):
        with pytest.raises(HTTPError):
            get_current_congress()

    assert not any("test-key" in record.getMessage() for record in caplog.records)
