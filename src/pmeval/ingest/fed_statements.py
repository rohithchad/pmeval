"""Ingest FOMC policy statements from the Federal Reserve Board website.

Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm lists each
meeting and links to its statement. Board content is public domain ("may be copied and
distributed without permission, please cite the Board as the source"). There is no
robots.txt and no registration. We still fetch politely: one request per second, an
identifying User-Agent, and each statement is fetched once (progress is saved).

Each statement page states its release time ("For release at 2:00 p.m. EDT"), which
is stored as text so staging can build the publication timestamp.

Run:  python -m pmeval.ingest.fed_statements
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup

from pmeval.config import get_settings
from pmeval.ingest.http import HttpClient
from pmeval.ingest.raw import write_raw
from pmeval.ingest.state import ProgressState
from pmeval.logging_setup import setup_logging

logger = logging.getLogger(__name__)

SOURCE = "fed"
FED_BASE_URL = "https://www.federalreserve.gov"
CALENDAR_PATH = "/monetarypolicy/fomccalendars.htm"
USER_AGENT = "pmeval-research/0.1 (portfolio project; public data)"

# Statement links look like /newsevents/pressreleases/monetary20260128a.htm
STATEMENT_HREF = re.compile(r"^/newsevents/pressreleases/monetary\d{8}a\.htm$")
STATEMENT_LINK = re.compile(r'href="(/newsevents/pressreleases/monetary(\d{8})a\.htm)"')


def make_fed_http_client() -> HttpClient:
    """HTTP client with an identifying User-Agent and a 1 request/second limit."""
    client = HttpClient(FED_BASE_URL, min_interval_seconds=1.0)
    client.session.headers["User-Agent"] = USER_AGENT
    return client


def find_statement_links(calendar_html: str) -> list[tuple[str, str]]:
    """Return unique (path, YYYYMMDD) pairs for statements linked from the calendar page."""
    seen: dict[str, str] = {}
    for path, date in STATEMENT_LINK.findall(calendar_html):
        seen[path] = date
    return sorted(seen.items(), key=lambda item: item[1])


def parse_statement(html: str, path: str) -> dict:
    """Extract the fields staging needs from one statement page.

    Returns url path, the date text, the 'For release at ...' line and the body text.
    """
    soup = BeautifulSoup(html, "html.parser")
    date_tag = soup.select_one("p.article__time")
    article = soup.select_one("div#article")
    article_text = article.get_text(" ", strip=True) if article else ""
    release_match = re.search(r"For release at [^.]*\.m\.\s*\w+", article_text)
    return {
        "path": path,
        "date_text": date_tag.get_text(strip=True) if date_tag else None,
        "release_line": release_match.group(0) if release_match else None,
        "body_text": article_text,
    }


def parse_meetings(calendar_html: str) -> list[dict]:
    """Extract every FOMC meeting row (past and scheduled) from the calendar page.

    Values stay as the page shows them (for example month "April/May", date "29-30*");
    staging converts them to dates. `statement_path` is None for meetings without a statement.
    """
    soup = BeautifulSoup(calendar_html, "html.parser")
    meetings: list[dict] = []
    for panel in soup.select("div.panel"):
        heading = panel.select_one("h4")
        year_match = (
            re.match(r"(\d{4}) FOMC Meetings", heading.get_text(strip=True)) if heading else None
        )
        if not year_match:
            continue
        for row in panel.select("div.fomc-meeting"):
            month = row.select_one(".fomc-meeting__month")
            day = row.select_one(".fomc-meeting__date")
            link = row.find("a", href=STATEMENT_HREF)
            meetings.append(
                {
                    "year": int(year_match.group(1)),
                    "month_text": month.get_text(strip=True) if month else None,
                    "date_text": day.get_text(strip=True) if day else None,
                    "statement_path": link["href"] if link else None,
                }
            )
    return meetings


def ingest_statements(http: HttpClient, raw_dir: Path, state: ProgressState) -> int:
    """Fetch every statement not yet stored; returns how many were fetched."""
    calendar_html = http.get_text(CALENDAR_PATH)
    write_raw(raw_dir, SOURCE, "fomc_meetings", parse_meetings(calendar_html))
    links = find_statement_links(calendar_html)
    fetched = 0
    for path, _date in links:
        if state.is_done(path):
            continue
        row = parse_statement(http.get_text(path), path)
        write_raw(raw_dir, SOURCE, "fomc_statements", [row], {"path": path})
        state.mark_done(path)
        fetched += 1
    return fetched


def main() -> None:
    settings = get_settings()
    setup_logging(settings.pmeval_log_level)
    state = ProgressState(settings.pmeval_raw_dir / "_state" / "fed_statements.json")
    count = ingest_statements(make_fed_http_client(), settings.pmeval_raw_dir, state)
    logger.info("fetched %d new FOMC statements", count)


if __name__ == "__main__":
    main()
