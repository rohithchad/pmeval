"""Generate the dbt part of docs/data_dictionary.md from the dbt schema YAML files.

The YAML descriptions are the single source of truth, so the dictionary cannot drift from the
models. Only the text between the two marker comments is rewritten; the rest of the file is
hand-written.

Run:  python -m pmeval.warehouse.data_dictionary          (rewrite the file)
      python -m pmeval.warehouse.data_dictionary --check  (exit 1 if it is out of date)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
DBT_MODELS = ROOT / "dbt_project" / "models"
DICTIONARY = ROOT / "docs" / "data_dictionary.md"
START = "<!-- dbt:start -->"
END = "<!-- dbt:end -->"


def load_models(models_dir: Path) -> list[dict]:
    """Read every model and seed entry from all schema YAML files, sorted by name."""
    entries: list[dict] = []
    for path in sorted(models_dir.rglob("*.yml")):
        document = yaml.safe_load(path.read_text()) or {}
        for key in ("models", "seeds"):
            for entry in document.get(key, []):
                entries.append({**entry, "kind": key[:-1], "file": path.name})
    return sorted(entries, key=lambda entry: entry["name"])


def describe_tests(tests: list) -> str:
    """Turn a list of dbt tests (strings or one-key dicts) into a short comma-separated text."""
    names = [item if isinstance(item, str) else next(iter(item)) for item in tests or []]
    return ", ".join(names)


def render_entry(entry: dict) -> str:
    """Markdown for one model: its description, model-level tests and a column table."""
    lines = [f"### `{entry['name']}` ({entry['kind']})", ""]
    lines.append(" ".join(str(entry.get("description", "")).split()))
    model_tests = describe_tests(entry.get("data_tests"))
    if model_tests:
        lines += ["", f"Model tests: {model_tests}"]
    columns = entry.get("columns") or []
    if columns:
        lines += ["", "| Column | Description | Tests |", "|--------|-------------|-------|"]
        for column in columns:
            description = " ".join(str(column.get("description", "")).split())
            tests = describe_tests(column.get("data_tests"))
            lines.append(f"| `{column['name']}` | {description} | {tests} |")
    return "\n".join(lines)


def render_dbt_section(models_dir: Path = DBT_MODELS) -> str:
    """The full generated block, without the marker comments."""
    return "\n\n".join(render_entry(entry) for entry in load_models(models_dir))


def replace_section(document: str, generated: str) -> str:
    """Swap the text between the markers for `generated`."""
    start = document.index(START) + len(START)
    end = document.index(END)
    return document[:start] + "\n\n" + generated + "\n\n" + document[end:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    args = parser.parse_args()
    current = DICTIONARY.read_text()
    updated = replace_section(current, render_dbt_section())
    if args.check:
        sys.exit(0 if updated == current else 1)
    DICTIONARY.write_text(updated)


if __name__ == "__main__":
    main()
