"""Small timestamp helpers shared by the ingestion modules."""

from __future__ import annotations

from datetime import UTC, datetime


def parse_iso_to_unix(value: str | None) -> int | None:
    """Convert an ISO 8601 string such as '2025-01-02T13:30:00Z' to Unix seconds (UTC)."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())
