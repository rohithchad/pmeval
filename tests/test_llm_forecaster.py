import json
from datetime import datetime, timedelta

import duckdb
import pytest

from pmeval.forecast.base import LeakageError, Target
from pmeval.forecast.llm import (
    CallLog,
    LlmForecaster,
    SimpleResponse,
    StubClient,
    assert_inputs_as_of,
    build_event_inputs,
    hash_inputs,
    load_template,
    parse_response,
    render_prompt,
)
from pmeval.forecast.predictions import build_predictions

FORECAST = datetime(2026, 6, 4, 12, 30)
RELEASE = datetime(2026, 6, 5, 12, 30)
MARKET_PRICE = 0.7312  # a distinctive number: it must never appear in a prompt


def make_target(ticker="E1-T4.0", strike=4.0, **feature_overrides) -> Target:
    features = {
        "strike": strike,
        "contract_code": "T4.0",
        "contract_bps": None,
        "last_value": 4.0,
        "last_observation_date": datetime(2026, 4, 1),
        "prev_value": 3.9,
        "mean_prior_12": 3.95,
        "last_value_available_at": datetime(2026, 5, 8, 12, 30),
        "prev_value_available_at": datetime(2026, 4, 3, 12, 30),
        "latest_statement_path": None,
        "latest_statement_published_at": None,
        "latest_feature_available_at": datetime(2026, 5, 8, 12, 30),
    }
    features.update(feature_overrides)
    return Target("E1", ticker, "unemployment", FORECAST, RELEASE, features, MARKET_PRICE)


class FakeClient:
    """Returns queued answers and counts calls. Never touches the network."""

    model = "fake-model"

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0
        self.prompts = []

    def complete(self, system, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleResponse(answer, 100, 20, self.model)


def answer(**probabilities) -> str:
    return json.dumps(
        {"forecasts": [{"market_ticker": k, "probability": v} for k, v in probabilities.items()]}
    )


@pytest.fixture
def log():
    return CallLog(duckdb.connect(":memory:"))


def forecaster(client, targets, log, **kwargs):
    return LlmForecaster(client, targets, {}, log, **kwargs)


# ---- parsing -------------------------------------------------------------------------------


def test_parse_accepts_exact_contract_set():
    assert parse_response(answer(a=0.2, b=0.9), ["a", "b"]) == {"a": 0.2, "b": 0.9}


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        answer(a=1.5),  # out of range
        answer(a=0.5),  # missing contract b
        answer(a=0.5, b=0.5, c=0.5),  # extra contract
        json.dumps({"forecasts": []}),
        json.dumps({"other": 1}),
    ],
)
def test_parse_rejects_bad_output(text):
    with pytest.raises(ValueError):
        parse_response(text, ["a", "b"])


# ---- averaging and logging -----------------------------------------------------------------


def test_samples_are_averaged_and_every_call_logged(log):
    target = make_target()
    client = FakeClient([answer(**{target.market_ticker: p}) for p in (0.2, 0.4, 0.9)])
    f = forecaster(client, [target], log, n_samples=3)
    assert f.predict(target) == pytest.approx(0.5)
    rows = log.con.execute(
        "SELECT prompt_version, model, inputs_hash, raw_response, parsed_probabilities, "
        "requested_at, status, dry_run FROM forecast.llm_calls ORDER BY sample_index"
    ).fetchall()
    assert len(rows) == 3
    for version, model, digest, raw, parsed, requested_at, status, dry in rows:
        assert version == "forecast_v1" and model == "fake-model"
        assert len(digest) == 64 and raw and parsed and requested_at
        assert status == "ok" and dry is False
    assert f.metadata(target)["samples"] == [0.2, 0.4, 0.9]
    assert f.version == "forecast_v1:fake-model"


def test_failures_are_logged_and_other_samples_still_count(log):
    target = make_target()
    client = FakeClient([RuntimeError("boom"), "garbage", answer(**{target.market_ticker: 0.6})])
    f = forecaster(client, [target], log, n_samples=3)
    assert f.predict(target) == pytest.approx(0.6)
    statuses = [
        r[0]
        for r in log.con.execute(
            "SELECT status FROM forecast.llm_calls ORDER BY sample_index"
        ).fetchall()
    ]
    assert statuses == ["error", "invalid", "ok"]


def test_declines_when_every_sample_fails(log):
    target = make_target()
    f = forecaster(FakeClient([RuntimeError("x")] * 2), [target], log, n_samples=2)
    assert f.predict(target) is None


def test_one_call_set_covers_all_contracts_of_an_event(log):
    low, high = make_target("E1-T3.8", 3.8), make_target("E1-T4.2", 4.2)
    client = FakeClient([answer(**{"E1-T3.8": 0.8, "E1-T4.2": 0.3})])
    f = forecaster(client, [low, high], log, n_samples=1)
    assert f.predict(low) == 0.8 and f.predict(high) == 0.3
    assert client.calls == 1


def test_rerun_reuses_logged_responses_without_calling_again(log):
    target = make_target()
    first = FakeClient([answer(**{target.market_ticker: 0.4})])
    forecaster(first, [target], log, n_samples=1).predict(target)
    second = FakeClient([])  # would raise IndexError if called
    assert forecaster(second, [target], log, n_samples=1).predict(target) == 0.4
    assert second.calls == 0


def test_events_outside_the_given_set_are_declined(log):
    f = forecaster(FakeClient([]), [make_target()], log)
    other = Target("OTHER", "OTHER-T1", "unemployment", FORECAST, RELEASE, {})
    assert f.predict(other) is None


# ---- no look-ahead ---------------------------------------------------------------------------


def test_leakage_guard_blocks_call_when_input_is_after_forecast_time(log):
    leaky = make_target(last_value_available_at=FORECAST + timedelta(minutes=1))
    client = FakeClient([])
    f = forecaster(client, [leaky], log)
    with pytest.raises(LeakageError):
        f.predict(leaky)
    assert client.calls == 0  # nothing was sent


def test_statement_published_after_forecast_time_is_rejected():
    target = make_target(latest_statement_path="/s.htm", latest_statement_published_at=FORECAST)
    statements = {"/s.htm": (FORECAST + timedelta(hours=1), "text")}
    inputs = build_event_inputs([target], statements)
    with pytest.raises(LeakageError):
        assert_inputs_as_of(inputs)


def test_equal_timestamp_is_allowed():
    inputs = build_event_inputs([make_target(last_value_available_at=FORECAST)], {})
    assert_inputs_as_of(inputs)


# ---- prompt ----------------------------------------------------------------------------------


def test_prompt_has_forecast_time_figures_and_no_market_price():
    target = make_target()
    inputs = build_event_inputs([target], {})
    prompt = render_prompt(inputs, load_template("forecast_v1"))
    assert "2026-06-04 12:30:00 UTC" in prompt
    assert "Latest published figure: 4" in prompt
    assert "E1-T4.0 | YES if the published figure is above 4.0" in prompt
    assert str(MARKET_PRICE) not in prompt and "0.73" not in prompt
    assert "{" not in prompt  # every placeholder was filled


def test_fed_prompt_includes_statement_text_and_action_meaning():
    target = Target(
        "F1",
        "F1-C25",
        "fed",
        FORECAST,
        RELEASE,
        {
            "contract_bps": -25,
            "contract_code": "C25",
            "latest_statement_path": "/s.htm",
            "latest_statement_published_at": datetime(2026, 4, 29, 18, 0),
        },
    )
    statements = {"/s.htm": (datetime(2026, 4, 29, 18, 0), "The Committee decided to hold.")}
    prompt = render_prompt(build_event_inputs([target], statements), load_template("forecast_v1"))
    assert "The Committee decided to hold." in prompt
    assert "cuts the target range by 25 bps" in prompt


def test_inputs_hash_changes_with_inputs_and_prompt_version():
    base = build_event_inputs([make_target()], {})
    changed = build_event_inputs([make_target(last_value=4.1)], {})
    assert hash_inputs(base, "v1") == hash_inputs(base, "v1")
    assert hash_inputs(base, "v1") != hash_inputs(changed, "v1")
    assert hash_inputs(base, "v1") != hash_inputs(base, "v2")


# ---- dry run ---------------------------------------------------------------------------------


def test_dry_run_uses_stub_and_separate_forecaster_name(log):
    low, high = make_target("E1-T3.8", 3.8), make_target("E1-T4.2", 4.2)
    f = forecaster(StubClient(), [low, high], log, n_samples=1, dry_run=True)
    assert f.name == "llm_dry_run"
    frame = build_predictions(f, [low, high], made_at=FORECAST, mode="backtest")
    assert frame["probability"].tolist() == [0.5, 0.5]
    assert set(frame["forecaster"]) == {"llm_dry_run"}
    assert log.con.execute("SELECT dry_run FROM forecast.llm_calls").fetchone() == (True,)


def test_real_forecaster_name_is_llm(log):
    assert forecaster(FakeClient([]), [make_target()], log).name == "llm"
