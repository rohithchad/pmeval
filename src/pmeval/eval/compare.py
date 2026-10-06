"""Is one forecaster really better than another? Cluster bootstrap and a Diebold-Mariano test.

Why "cluster": contracts of one release (for example the strikes 4.0, 4.2, 4.4 of the same jobs
report) are driven by one number and resolve together, so they are not independent. Treating them as
independent would make results look far more certain than they are. We therefore resample whole
events, never single contracts.

Everything here compares LOSS differences, d = loss(A) - loss(B), on the contracts both forecasters
forecast. Negative d means A is better. See docs/DECISIONS.md D15 for assumptions and limits.

Tables written: eval.leaderboard and eval.comparisons.

Run:  python -m pmeval.eval.compare
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

from pmeval.config import get_settings
from pmeval.eval.scoring import add_scores, load_scoring_frame
from pmeval.logging_setup import setup_logging
from pmeval.warehouse.db import connect

logger = logging.getLogger(__name__)

N_BOOTSTRAP = 10_000
ALPHA = 0.05
SEED = 20260101
SMALL_SAMPLE_EVENTS = 30  # below this many events, treat all intervals and p-values as rough
METRICS = ("brier", "log_loss")
EXCLUDED_FORECASTERS = {"llm_dry_run"}  # stub output is not a forecast


@dataclass(frozen=True)
class Interval:
    """Point estimate with a percentile bootstrap interval."""

    estimate: float
    lower: float
    upper: float


def event_sums(frame: pd.DataFrame, value_column: str) -> tuple[np.ndarray, np.ndarray]:
    """Per event: the sum of `value_column` and its contract count, events in release order."""
    ordered = frame.sort_values(["release_at", "event_id"])
    grouped = ordered.groupby("event_id", sort=False)[value_column].agg(["sum", "size"])
    return grouped["sum"].to_numpy(dtype=float), grouped["size"].to_numpy(dtype=float)


def cluster_bootstrap_mean(
    sums: np.ndarray,
    counts: np.ndarray,
    n_boot: int = N_BOOTSTRAP,
    alpha: float = ALPHA,
    seed: int = SEED,
) -> tuple[Interval, np.ndarray]:
    """Mean over contracts with a cluster bootstrap interval; also returns the bootstrap means.

    Each replicate draws as many events as the sample has, with replacement, and recomputes the
    contract-weighted mean (total value / total contracts) over the drawn events.
    """
    n_events = len(sums)
    estimate = float(sums.sum() / counts.sum())
    if n_events < 2:
        nan = float("nan")
        return Interval(estimate, nan, nan), np.array([])
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n_events, size=(n_boot, n_events))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    lower, upper = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return Interval(estimate, float(lower), float(upper)), means


def bootstrap_p_value(means: np.ndarray) -> float:
    """Two-sided p-value for "the true mean difference is 0" from the bootstrap distribution.

    Twice the smaller tail mass beyond zero. With a finite number of replicates it cannot go below
    about 2 / n_boot.
    """
    if means.size == 0:
        return float("nan")
    below = (means <= 0).mean()
    above = (means >= 0).mean()
    return float(min(1.0, 2 * min(below, above)))


def diebold_mariano(event_diffs: np.ndarray) -> tuple[float, float]:
    """Diebold-Mariano test (one-step horizon) with the Harvey-Leybourne-Newbold correction.

    `event_diffs` holds one loss differential per event (the mean over that event's contracts),
    in release order. The statistic is mean(d) / sqrt(var(d) / n), scaled by sqrt((n - 1) / n)
    for small samples and compared with a t distribution with n - 1 degrees of freedom. At a
    one-step horizon no autocovariance terms enter the variance. Returns (statistic, two-sided
    p-value); NaN when there are fewer than 3 events or no variation.
    """
    n = len(event_diffs)
    if n < 3:
        return float("nan"), float("nan")
    variance = float(np.var(event_diffs, ddof=1))
    if variance < 1e-18:  # identical differentials, up to floating-point noise
        return float("nan"), float("nan")
    dm = float(np.mean(event_diffs)) / np.sqrt(variance / n)
    corrected = dm * np.sqrt((n - 1) / n)
    p_value = 2 * float(stats.t.sf(abs(corrected), df=n - 1))
    return float(corrected), p_value


def latest_versions(scored: pd.DataFrame) -> pd.DataFrame:
    """Keep, for each forecaster and mode, only the version of its most recent prediction."""
    newest = (
        scored.sort_values("made_at")
        .groupby(["forecaster", "mode"])["version"]
        .last()
        .rename("newest_version")
        .reset_index()
    )
    merged = scored.merge(newest, on=["forecaster", "mode"])
    return merged[merged["version"] == merged["newest_version"]].drop(columns="newest_version")


def common_support(frame: pd.DataFrame, forecasters: list[str]) -> pd.DataFrame:
    """Rows for the contracts that EVERY listed forecaster predicted, so scores are comparable."""
    counts = (
        frame[frame["forecaster"].isin(forecasters)]
        .groupby(["event_id", "market_ticker"])["forecaster"]
        .nunique()
    )
    keep = counts[counts == len(forecasters)].index
    index = frame.set_index(["event_id", "market_ticker"]).index
    return frame[index.isin(keep) & frame["forecaster"].isin(forecasters).to_numpy()]


def leaderboard(scored: pd.DataFrame) -> pd.DataFrame:
    """Mean Brier and log loss per forecaster with cluster-bootstrap intervals.

    For each mode, forecasters are compared on the contracts all of them forecast.
    """
    rows = []
    for mode, group in scored.groupby("mode"):
        forecasters = sorted(group["forecaster"].unique())
        shared = common_support(group, forecasters)
        for forecaster, own in shared.groupby("forecaster"):
            for metric in METRICS:
                sums, counts = event_sums(own, metric)
                interval, _ = cluster_bootstrap_mean(sums, counts)
                rows.append(
                    {
                        "forecaster": forecaster,
                        "version": own["version"].iloc[0],
                        "mode": mode,
                        "metric": metric,
                        "n_events": len(sums),
                        "n_contracts": int(counts.sum()),
                        "mean_score": interval.estimate,
                        "ci_lower": interval.lower,
                        "ci_upper": interval.upper,
                        "possibly_contaminated": bool(own["possibly_contaminated"].iloc[0]),
                        "small_sample": len(sums) < SMALL_SAMPLE_EVENTS,
                    }
                )
    return pd.DataFrame(rows)


def compare_pair(group: pd.DataFrame, a: str, b: str, metric: str) -> dict | None:
    """Compare forecasters a and b on the contracts both forecast; None if they share none."""
    shared = common_support(group, [a, b])
    if shared.empty:
        return None
    wide = shared.pivot_table(
        index=["event_id", "market_ticker", "release_at"], columns="forecaster", values=metric
    ).reset_index()
    wide["diff"] = wide[a] - wide[b]
    sums, counts = event_sums(wide, "diff")
    interval, means = cluster_bootstrap_mean(sums, counts)
    event_means = sums / counts
    dm_stat, dm_p = diebold_mariano(event_means)
    return {
        "forecaster_a": a,
        "forecaster_b": b,
        "metric": metric,
        "n_events": len(sums),
        "n_contracts": int(counts.sum()),
        "mean_diff": interval.estimate,
        "ci_lower": interval.lower,
        "ci_upper": interval.upper,
        "bootstrap_p_value": bootstrap_p_value(means),
        "dm_statistic": dm_stat,
        "dm_p_value": dm_p,
        "small_sample": len(sums) < SMALL_SAMPLE_EVENTS,
    }


def comparisons(scored: pd.DataFrame) -> pd.DataFrame:
    """All pairwise comparisons per mode, for both metrics. mean_diff < 0 means A beats B."""
    rows = []
    for mode, group in scored.groupby("mode"):
        forecasters = sorted(group["forecaster"].unique())
        for a, b in itertools.combinations(forecasters, 2):
            for metric in METRICS:
                result = compare_pair(group, a, b, metric)
                if result:
                    rows.append(
                        {
                            "mode": mode,
                            **result,
                            "possibly_contaminated": any(
                                name == "llm" and mode == "backtest" for name in (a, b)
                            ),
                        }
                    )
    return pd.DataFrame(rows)


def prepare(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Scored predictions for real forecasters only, latest version of each."""
    scored = add_scores(load_scoring_frame(con))
    scored = scored[~scored["forecaster"].isin(EXCLUDED_FORECASTERS)]
    return latest_versions(scored) if len(scored) else scored


def write_comparisons(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """Rebuild eval.leaderboard and eval.comparisons. Returns their row counts."""
    scored = prepare(con)
    board = leaderboard(scored) if len(scored) else pd.DataFrame()
    pairs = comparisons(scored) if len(scored) else pd.DataFrame()
    con.execute("CREATE SCHEMA IF NOT EXISTS eval")
    for name, frame in [("leaderboard", board), ("comparisons", pairs)]:
        con.register("frame_to_write", frame)
        con.execute(f"CREATE OR REPLACE TABLE eval.{name} AS SELECT * FROM frame_to_write")
        con.unregister("frame_to_write")
    return len(board), len(pairs)


def main() -> None:
    settings = get_settings()
    setup_logging(settings.pmeval_log_level)
    con = connect()
    try:
        board_rows, pair_rows = write_comparisons(con)
        logger.info("wrote %d leaderboard rows and %d comparisons", board_rows, pair_rows)
    finally:
        con.close()


if __name__ == "__main__":
    main()
