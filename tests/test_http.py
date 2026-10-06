import pytest
import requests
import responses

from pmeval.ingest.http import HttpClient, HttpError

URL = "https://api.test/v2/thing"


@responses.activate
def test_retries_then_succeeds_with_exponential_backoff(make_client, sleeps):
    responses.add(responses.GET, URL, status=429)
    responses.add(responses.GET, URL, status=503)
    responses.add(responses.GET, URL, json={"ok": True})
    assert make_client().get_json("/thing") == {"ok": True}
    assert len(responses.calls) == 3
    # delays double (1s then 2s); jitter adds at most 10%
    assert 1.0 <= sleeps[0] <= 1.1
    assert 2.0 <= sleeps[1] <= 2.2


@responses.activate
def test_gives_up_after_max_retries(make_client):
    responses.add(responses.GET, URL, status=500)
    with pytest.raises(HttpError):
        make_client(max_retries=2).get_json("/thing")
    assert len(responses.calls) == 3  # first try + 2 retries


@responses.activate
def test_client_error_is_not_retried(make_client):
    responses.add(responses.GET, URL, status=404)
    with pytest.raises(HttpError):
        make_client().get_json("/thing")
    assert len(responses.calls) == 1


@responses.activate
def test_network_error_is_retried(make_client):
    responses.add(responses.GET, URL, body=requests.ConnectionError("boom"))
    responses.add(responses.GET, URL, json={"ok": 1})
    assert make_client().get_json("/thing") == {"ok": 1}


@responses.activate
def test_rate_limit_spaces_requests():
    waits: list[float] = []
    now = [0.0]
    client = HttpClient(
        "https://api.test/v2",
        min_interval_seconds=1.0,
        sleep=waits.append,
        clock=lambda: now[0],
    )
    responses.add(responses.GET, URL, json={})
    client.get_json("/thing")
    now[0] = 0.25  # only 0.25s later, so a 0.75s wait is needed
    client.get_json("/thing")
    assert waits == [pytest.approx(0.75)]


@responses.activate
def test_get_text_returns_body(make_client):
    responses.add(responses.GET, URL, body="<html>hi</html>")
    assert make_client().get_text("/thing") == "<html>hi</html>"
