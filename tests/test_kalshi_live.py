import responses

from pmeval.ingest.kalshi import CANDLE_WINDOW_SECONDS, KalshiClient

BASE = "https://api.test/v2"


@responses.activate
def test_markets_follow_cursor_until_empty(make_client, load_fixture):
    page1 = load_fixture("kalshi/markets_page1.json")
    page2 = load_fixture("kalshi/markets_page2_end.json")
    responses.add(responses.GET, f"{BASE}/markets", json=page1)
    responses.add(responses.GET, f"{BASE}/markets", json=page2)
    markets = list(KalshiClient(make_client(BASE)).iter_markets(series_ticker="KXU3"))
    assert len(markets) == len(page1["markets"]) + len(page2["markets"])
    # the second request must pass the first page's cursor back unchanged
    assert f"cursor={page1['cursor']}" in responses.calls[1].request.url


@responses.activate
def test_series_and_events_shapes(make_client, load_fixture):
    responses.add(
        responses.GET, f"{BASE}/series/KXU3", json=load_fixture("kalshi/series_KXU3.json")
    )
    responses.add(responses.GET, f"{BASE}/events", json=load_fixture("kalshi/events_KXU3.json"))
    client = KalshiClient(make_client(BASE))
    assert client.get_series("KXU3")["ticker"] == "KXU3"
    assert all("event_ticker" in e for e in client.iter_events("KXU3"))


@responses.activate
def test_candlesticks_are_requested_in_windows(make_client, load_fixture):
    candles = load_fixture("kalshi/candlesticks_live.json")
    url = f"{BASE}/series/KXU3/markets/KXU3-26NOV-T5.0/candlesticks"
    responses.add(responses.GET, url, json=candles)
    responses.add(responses.GET, url, json=candles)
    start = 1_000_000
    end = start + CANDLE_WINDOW_SECONDS + 10  # needs two windows
    result = KalshiClient(make_client(BASE)).get_candlesticks(
        "KXU3", "KXU3-26NOV-T5.0", start, end, 60
    )
    assert len(responses.calls) == 2
    assert len(result) == 2 * len(candles["candlesticks"])
    assert "period_interval=60" in responses.calls[0].request.url


def test_invalid_candle_period_rejected(make_client):
    import pytest

    with pytest.raises(ValueError):
        KalshiClient(make_client(BASE)).get_candlesticks("S", "T", 0, 1, period_minutes=5)


@responses.activate
def test_trades_use_ticker_filter(make_client, load_fixture):
    responses.add(
        responses.GET, f"{BASE}/markets/trades", json=load_fixture("kalshi/trades_live.json")
    )
    trades = list(KalshiClient(make_client(BASE)).iter_trades("KXU3-26NOV-T5.0"))
    assert trades and "ticker=KXU3-26NOV-T5.0" in responses.calls[0].request.url
