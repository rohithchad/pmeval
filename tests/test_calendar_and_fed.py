import json

import pandas as pd
import responses
from pydantic import SecretStr

from pmeval.config import SERIES_REGISTRY
from pmeval.ingest.fed_statements import (
    find_statement_links,
    ingest_statements,
    parse_meetings,
    parse_statement,
)
from pmeval.ingest.fred import FredClient
from pmeval.ingest.release_calendar import ingest_release_dates
from pmeval.ingest.state import ProgressState


@responses.activate
def test_release_calendar_rows_carry_time_assumption(make_client, tmp_path):
    responses.add(
        responses.GET,
        "https://api.test/fred/release/dates",
        json={"release_dates": [{"release_id": 10, "date": "2026-10-14"}]},
    )
    cpi = next(s for s in SERIES_REGISTRY if s.key == "cpi")
    client = FredClient(make_client("https://api.test/fred"), SecretStr("k"))
    assert ingest_release_dates(client, tmp_path, cpi) == 1
    frame = pd.concat(pd.read_parquet(p) for p in tmp_path.rglob("*.parquet"))
    row = json.loads(frame.loc[0, "payload"])
    assert row["release_date"] == "2026-10-14" and row["release_time_local"] == "08:30"


def test_fed_series_has_no_fred_calendar(make_client, tmp_path):
    fed = next(s for s in SERIES_REGISTRY if s.key == "fed")
    assert ingest_release_dates(None, tmp_path, fed) == 0


def test_statement_links_found_and_deduplicated(fixtures_dir):
    html = (fixtures_dir / "fed_calendar_excerpt.html").read_text()
    links = find_statement_links(html + html)
    assert len(links) == len({path for path, _ in links}) == 4


def test_meetings_parsed(fixtures_dir):
    meetings = parse_meetings((fixtures_dir / "fed_calendar_meetings.html").read_text())
    assert meetings[0] == {
        "year": 2026,
        "month_text": "January",
        "date_text": "27-28",
        "statement_path": "/newsevents/pressreleases/monetary20260128a.htm",
    }
    assert meetings[1]["date_text"] == "17-18*"


def test_statement_parsed(fixtures_dir):
    parsed = parse_statement((fixtures_dir / "fed_statement_sample.html").read_text(), "/p.htm")
    assert parsed["release_line"] == "For release at 2:00 p.m. EDT"
    assert parsed["date_text"] == "September 16, 2026"
    assert "Federal Open Market Committee" in parsed["body_text"]


@responses.activate
def test_statements_fetched_once(make_client, fixtures_dir, tmp_path):
    base = "https://fed.test"
    calendar = (fixtures_dir / "fed_calendar_excerpt.html").read_text()
    statement = (fixtures_dir / "fed_statement_sample.html").read_text()
    responses.add(responses.GET, f"{base}/monetarypolicy/fomccalendars.htm", body=calendar)
    responses.add(
        responses.GET, url=__import__("re").compile(f"{base}/newsevents/.*"), body=statement
    )
    state = ProgressState(tmp_path / "state.json")
    client = make_client(base)
    assert ingest_statements(client, tmp_path, state) == 4
    assert ingest_statements(client, tmp_path, state) == 0  # second run fetches nothing new
