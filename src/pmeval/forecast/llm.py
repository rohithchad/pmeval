"""LLM forecaster: asks a Claude model for probabilities using only as-of inputs.

Design rules (see docs/DECISIONS.md D14):
- One call per event covers all of its contracts, which keeps cost down and makes the answers
  mutually consistent. Each call is repeated `n_samples` times and the probabilities are averaged.
- The prompt is a versioned file (forecast_v1.txt). The model is NOT shown the market price, so it
  is an independent forecaster rather than a copy of the market.
- Every input has a timestamp at or before forecast_time. `assert_inputs_as_of` raises
  LeakageError before any call is made if one is later.
- Every call (including failures) is logged to forecast.llm_calls: prompt version, model, inputs
  hash, full prompt, raw response, parsed probabilities, token counts and timestamp. A rerun with
  the same inputs reuses the logged response instead of paying for a new call.
- Dry-run mode uses a stub client that never touches the network. Its predictions are stored under
  the forecaster name `llm_dry_run` so they can never be mistaken for real LLM forecasts.
- Predictions for events that were already resolved (mode = backtest) may be contaminated by the
  model's training data; the evaluation and dashboard label them "possibly contaminated".
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any, Protocol

import duckdb
import jsonschema
import pandas as pd

from pmeval.forecast.base import Forecaster, LeakageError, Target

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent / "prompts"
DEFAULT_PROMPT_VERSION = "forecast_v1"

SYSTEM_PROMPT = (
    "You forecast the outcomes of scheduled US economic releases and answer only with the "
    "requested JSON."
)

SERIES_DESCRIPTIONS = {
    "unemployment": (
        "US unemployment rate (Employment Situation report)",
        "the unemployment rate, in percent",
    ),
    "payrolls": (
        "US nonfarm payrolls (Employment Situation report)",
        "the monthly change in total nonfarm payroll employment, in jobs",
    ),
    "cpi": ("US Consumer Price Index", "the year-over-year percent change in CPI, to one decimal"),
    "gdp": (
        "US real GDP (advance estimate)",
        "annualized quarter-over-quarter real GDP growth, in percent",
    ),
    "fed": (
        "FOMC interest-rate decision",
        "the change in the federal funds target range decided at the meeting",
    ),
}

# What the API is asked to produce. Numeric bounds are enforced afterwards by VALIDATION_SCHEMA.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "forecasts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "market_ticker": {"type": "string"},
                    "probability": {"type": "number"},
                },
                "required": ["market_ticker", "probability"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["forecasts"],
    "additionalProperties": False,
}

VALIDATION_SCHEMA: dict[str, Any] = json.loads(json.dumps(OUTPUT_SCHEMA))
VALIDATION_SCHEMA["properties"]["forecasts"]["items"]["properties"]["probability"] = {
    "type": "number",
    "minimum": 0,
    "maximum": 1,
}
VALIDATION_SCHEMA["properties"]["forecasts"]["minItems"] = 1


class LlmResponse(Protocol):
    """What a client returns for one call."""

    text: str
    input_tokens: int | None
    output_tokens: int | None
    model: str


@dataclass
class SimpleResponse:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    model: str


class LlmClient(Protocol):
    def complete(self, system: str, prompt: str) -> LlmResponse:
        """Send one prompt and return the structured-JSON text answer."""


class StubClient:
    """Dry-run client: answers 0.5 for every contract in the prompt, with no network access."""

    model = "stub-dry-run"

    def complete(self, system: str, prompt: str) -> LlmResponse:
        tickers = [
            line.split("|")[0].strip("- ").strip() for line in prompt.splitlines() if "|" in line
        ]
        body = {"forecasts": [{"market_ticker": t, "probability": 0.5} for t in tickers]}
        return SimpleResponse(json.dumps(body), 0, 0, self.model)


class AnthropicClient:
    """Real client using the Anthropic SDK. Reads the key from settings, never from code.

    Opus 5.5 always thinks and rejects sampling parameters, so samples differ only through the
    model's own variation. Structured output is requested with output_config.format.
    """

    def __init__(self, api_key: str, model: str, effort: str = "medium", max_tokens: int = 16000):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    def complete(self, system: str, prompt: str) -> LlmResponse:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"model refused the request ({response.stop_details})")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return SimpleResponse(
            text, response.usage.input_tokens, response.usage.output_tokens, response.model
        )


# ---- prompt assembly -------------------------------------------------------------------------


def load_template(version: str = DEFAULT_PROMPT_VERSION) -> str:
    """Read a prompt template file by version name."""
    return (PROMPT_DIR / f"{version}.txt").read_text()


def describe_contract(target: Target) -> str:
    """One line describing what YES means for a contract."""
    features = target.features
    if target.series_key == "fed":
        bps = features.get("contract_bps")
        if bps == 0:
            meaning = "YES if the Fed leaves the target range unchanged"
        elif bps is not None and bps > 0:
            meaning = f"YES if the Fed raises the target range by {abs(bps)} bps"
        else:
            meaning = f"YES if the Fed cuts the target range by {abs(bps)} bps"
        code = features.get("contract_code")
        return f"{target.market_ticker} | {meaning} (code {code}; 26 means more than 25 bps)"
    return f"{target.market_ticker} | YES if the published figure is above {features.get('strike')}"


def known_information(target: Target, statements: Mapping[str, tuple[datetime, str]]) -> list[str]:
    """Bullet lines of everything known at forecast_time for the event's series."""
    f = target.features
    lines: list[str] = []
    if f.get("last_value") is not None:
        lines.append(
            f"- Latest published figure: {f['last_value']:.4g} (period starting "
            f"{pd.Timestamp(f['last_observation_date']).date()}, published "
            f"{f['last_value_available_at']} UTC)"
        )
    if f.get("prev_value") is not None:
        lines.append(f"- Figure before that: {f['prev_value']:.4g}")
    if f.get("mean_prior_12") is not None:
        lines.append(f"- Average of up to 12 earlier figures: {f['mean_prior_12']:.4g}")
    path = f.get("latest_statement_path")
    if path and path in statements:
        published_at, body = statements[path]
        lines.append(f"- Latest FOMC statement (published {published_at} UTC):\n{body}")
    return lines or ["- No earlier figures are available."]


def build_event_inputs(
    targets: list[Target], statements: Mapping[str, tuple[datetime, str]]
) -> dict[str, Any]:
    """Assemble the structured as-of inputs for one event, with every timestamp listed.

    The `timestamps` entry is what the leakage guard checks.
    """
    first = targets[0]
    f = first.features
    timestamps = {
        key: f[key]
        for key in (
            "last_value_available_at",
            "prev_value_available_at",
            "latest_statement_published_at",
            "latest_feature_available_at",
        )
        if f.get(key) is not None
    }
    path = f.get("latest_statement_path")
    if path and path in statements:
        timestamps["statement_text_published_at"] = statements[path][0]
    return {
        "event_id": first.event_id,
        "series_key": first.series_key,
        "forecast_time": first.forecast_time,
        "release_at": first.release_at,
        "known_information": known_information(first, statements),
        "contracts": [describe_contract(t) for t in targets],
        "tickers": [t.market_ticker for t in targets],
        "timestamps": timestamps,
    }


def assert_inputs_as_of(inputs: Mapping[str, Any]) -> None:
    """Leakage guard: raise LeakageError if any input timestamp is after forecast_time."""
    limit = pd.Timestamp(inputs["forecast_time"])
    for name, value in inputs["timestamps"].items():
        if pd.Timestamp(value) > limit:
            raise LeakageError(
                f"{inputs['event_id']}: input {name}={value} is after forecast_time={limit}"
            )


def hash_inputs(inputs: Mapping[str, Any], prompt_version: str) -> str:
    """Stable fingerprint of the structured inputs plus the prompt version."""
    payload = json.dumps({"v": prompt_version, "inputs": inputs}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def render_prompt(inputs: Mapping[str, Any], template: str) -> str:
    """Fill the template. $-style placeholders avoid clashes with braces in the text."""
    name, figure = SERIES_DESCRIPTIONS[inputs["series_key"]]
    values = {
        "forecast_time": inputs["forecast_time"],
        "release_at": inputs["release_at"],
        "series_name": name,
        "figure_description": figure,
        "known_information": "\n".join(inputs["known_information"]),
        "contracts": "\n".join(f"- {line}" for line in inputs["contracts"]),
    }
    # The template file uses {name}; convert to $name so Template can substitute safely.
    converted = template
    for key in values:
        converted = converted.replace("{" + key + "}", "$" + key)
    return Template(converted).substitute(values)


def parse_response(text: str, expected_tickers: list[str]) -> dict[str, float]:
    """Parse and validate model output; raise ValueError unless it covers exactly the tickers."""
    try:
        data = json.loads(text)
        jsonschema.validate(data, VALIDATION_SCHEMA)
    except (json.JSONDecodeError, jsonschema.ValidationError) as error:
        raise ValueError(f"invalid model output: {error}") from error
    probabilities = {
        item["market_ticker"]: float(item["probability"]) for item in data["forecasts"]
    }
    if set(probabilities) != set(expected_tickers) or len(data["forecasts"]) != len(
        expected_tickers
    ):
        raise ValueError("model output does not cover exactly the requested contracts")
    return probabilities


# ---- call log --------------------------------------------------------------------------------

CREATE_LOG_SQL = """
CREATE SCHEMA IF NOT EXISTS forecast;
CREATE TABLE IF NOT EXISTS forecast.llm_calls (
    call_id               VARCHAR PRIMARY KEY,
    event_id              VARCHAR   NOT NULL,
    forecast_time         TIMESTAMP NOT NULL,
    prompt_version        VARCHAR   NOT NULL,
    model                 VARCHAR   NOT NULL,
    dry_run               BOOLEAN   NOT NULL,
    inputs_hash           VARCHAR   NOT NULL,
    sample_index          INTEGER   NOT NULL,
    requested_at          TIMESTAMP NOT NULL,
    status                VARCHAR   NOT NULL,
    prompt_text           VARCHAR,
    raw_response          VARCHAR,
    parsed_probabilities  VARCHAR,
    input_tokens          BIGINT,
    output_tokens         BIGINT,
    error_message         VARCHAR
);
"""


class CallLog:
    """Writes every LLM call to forecast.llm_calls and finds earlier successful ones."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con
        con.execute(CREATE_LOG_SQL)

    def find_ok(
        self,
        event_id: str,
        prompt_version: str,
        model: str,
        inputs_hash: str,
        sample_index: int,
        dry_run: bool,
    ) -> dict[str, float] | None:
        row = self.con.execute(
            "SELECT parsed_probabilities FROM forecast.llm_calls WHERE status = 'ok' AND "
            "event_id = ? AND prompt_version = ? AND model = ? AND inputs_hash = ? "
            "AND sample_index = ? AND dry_run = ? ORDER BY requested_at LIMIT 1",
            [event_id, prompt_version, model, inputs_hash, sample_index, dry_run],
        ).fetchone()
        return json.loads(row[0]) if row else None

    def record(self, **fields: Any) -> None:
        columns = ["call_id", *fields]
        values = [str(uuid.uuid4()), *fields.values()]
        marks = ", ".join("?" for _ in columns)
        self.con.execute(
            f"INSERT INTO forecast.llm_calls ({', '.join(columns)}) VALUES ({marks})", values
        )


# ---- the forecaster --------------------------------------------------------------------------


class LlmForecaster(Forecaster):
    """Averages `n_samples` structured answers per event. Declines events where every call fails.

    Args:
        client: AnthropicClient, or StubClient for dry runs.
        targets: all contracts that may be forecast; grouped by event so one call covers an event.
        statements: path -> (published_at, text) for FOMC statements.
        log: CallLog used for auditing and reuse.
        dry_run: True when the client is the stub; changes the forecaster name.
    """

    def __init__(
        self,
        client: LlmClient,
        targets: list[Target],
        statements: Mapping[str, tuple[datetime, str]],
        log: CallLog,
        n_samples: int = 3,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
        dry_run: bool = False,
    ) -> None:
        self.client = client
        self.statements = statements
        self.log = log
        self.n_samples = n_samples
        self.prompt_version = prompt_version
        self.dry_run = dry_run
        self.name = "llm_dry_run" if dry_run else "llm"
        self.version = f"{prompt_version}:{client.model}"
        self.template = load_template(prompt_version)
        self._by_event: dict[str, list[Target]] = defaultdict(list)
        for target in targets:
            self._by_event[target.event_id].append(target)
        self._results: dict[str, tuple[dict[str, list[float]], str]] = {}

    def _forecast_event(self, event_id: str) -> tuple[dict[str, list[float]], str]:
        """Run (or reuse) all samples for an event; returns ({ticker: [probabilities]}, hash)."""
        if event_id in self._results:
            return self._results[event_id]
        event_targets = self._by_event[event_id]
        inputs = build_event_inputs(event_targets, self.statements)
        assert_inputs_as_of(inputs)
        inputs_hash = hash_inputs(inputs, self.prompt_version)
        prompt = render_prompt(inputs, self.template)
        samples: dict[str, list[float]] = defaultdict(list)
        for index in range(self.n_samples):
            parsed = self._one_sample(inputs, inputs_hash, prompt, index)
            for ticker, probability in (parsed or {}).items():
                samples[ticker].append(probability)
        self._results[event_id] = (dict(samples), inputs_hash)
        return self._results[event_id]

    def _one_sample(
        self, inputs: Mapping[str, Any], inputs_hash: str, prompt: str, index: int
    ) -> dict[str, float] | None:
        event_id = inputs["event_id"]
        model = self.client.model
        logged = self.log.find_ok(
            event_id, self.prompt_version, model, inputs_hash, index, self.dry_run
        )
        if logged is not None:
            return logged
        base = {
            "event_id": event_id,
            "forecast_time": pd.Timestamp(inputs["forecast_time"]).to_pydatetime(),
            "prompt_version": self.prompt_version,
            "model": model,
            "dry_run": self.dry_run,
            "inputs_hash": inputs_hash,
            "sample_index": index,
            "requested_at": datetime.now(UTC).replace(tzinfo=None),
            "prompt_text": prompt,
        }
        try:
            response = self.client.complete(SYSTEM_PROMPT, prompt)
        except Exception as error:  # network, auth, refusal: log it and carry on
            logger.warning("LLM call failed for %s: %s", event_id, type(error).__name__)
            self.log.record(
                **base, status="error", error_message=f"{type(error).__name__}: {error}"
            )
            return None
        try:
            parsed = parse_response(response.text, inputs["tickers"])
        except ValueError as error:
            self.log.record(
                **base,
                status="invalid",
                raw_response=response.text,
                error_message=str(error),
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
            return None
        self.log.record(
            **base,
            status="ok",
            raw_response=response.text,
            parsed_probabilities=json.dumps(parsed, sort_keys=True),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        return parsed

    def predict(self, target: Target) -> float | None:
        if target.event_id not in self._by_event:
            return None  # event outside the (possibly cost-limited) set this forecaster was given
        samples, _ = self._forecast_event(target.event_id)
        values = samples.get(target.market_ticker)
        return sum(values) / len(values) if values else None

    def metadata(self, target: Target) -> dict[str, Any]:
        samples, inputs_hash = self._forecast_event(target.event_id)
        return {
            "model": self.client.model,
            "prompt_version": self.prompt_version,
            "inputs_hash": inputs_hash,
            "n_samples_requested": self.n_samples,
            "samples": samples.get(target.market_ticker, []),
            "dry_run": self.dry_run,
        }


def load_statements(con: duckdb.DuckDBPyConnection) -> dict[str, tuple[datetime, str]]:
    """FOMC statements as {path: (published_at, text)}. Inputs are filtered by time elsewhere."""
    rows = con.execute(
        "SELECT statement_path, published_at, body_text FROM staging.stg_fed__statements"
    ).fetchall()
    return {path: (published, text) for path, published, text in rows}
