import shutil
from datetime import datetime, timedelta

import duckdb
import numpy as np
import pandas as pd
import pytest
from scipy import stats

from pmeval.eval.compare import (
    bootstrap_p_value,
    cluster_bootstrap_mean,
    common_support,
    compare_pair,
    comparisons,
    diebold_mariano,
    event_sums,
    latest_versions,
    leaderboard,
    write_comparisons,
)
from pmeval.eval.scoring import add_scores
from pmeval.forecast.run import run


def sums_counts(per_event_diffs):
    sums = np.array([sum(d) for d in per_event_diffs], dtype=float)
    counts = np.array([len(d) for d in per_event_diffs], dtype=float)
    return sums, counts


def test_bootstrap_interval_contains_estimate_and_is_reproducible():
    rng = np.random.default_rng(1)
    diffs = [list(rng.normal(-0.02, 0.05, size=4)) for _ in range(60)]
    sums, counts = sums_counts(diffs)
    interval, means = cluster_bootstrap_mean(sums, counts, n_boot=2000, seed=7)
    again, _ = cluster_bootstrap_mean(sums, counts, n_boot=2000, seed=7)
    assert interval.lower < interval.estimate < interval.upper
    assert interval == again
    assert interval.upper < 0  # a clear, consistent advantage is detected
    assert bootstrap_p_value(means) < 0.05


def test_constant_difference_gives_degenerate_interval():
    sums, counts = sums_counts([[0.1, 0.1]] * 10)
    interval, _ = cluster_bootstrap_mean(sums, counts, n_boot=500)
    assert interval.lower == pytest.approx(0.1) and interval.upper == pytest.approx(0.1)


def test_clustering_widens_the_interval_when_contracts_move_together():
    """Contracts inside an event share one shock, so the honest interval is wider than naive."""
    rng = np.random.default_rng(3)
    shocks = rng.normal(0, 0.1, size=40)
    clustered = [[s] * 5 for s in shocks]  # five perfectly correlated contracts per event
    c_sums, c_counts = sums_counts(clustered)
    cluster_interval, _ = cluster_bootstrap_mean(c_sums, c_counts, n_boot=4000, seed=1)
    # naive: pretend each contract is its own event
    flat = [[x] for event in clustered for x in event]
    n_sums, n_counts = sums_counts(flat)
    naive_interval, _ = cluster_bootstrap_mean(n_sums, n_counts, n_boot=4000, seed=1)
    cluster_width = cluster_interval.upper - cluster_interval.lower
    naive_width = naive_interval.upper - naive_interval.lower
    assert cluster_width > 1.6 * naive_width


def test_interval_has_roughly_nominal_coverage_under_the_null():
    hits = 0
    runs = 150
    for seed in range(runs):
        rng = np.random.default_rng(seed)
        sums, counts = sums_counts([list(rng.normal(0, 0.1, size=3)) for _ in range(40)])
        interval, _ = cluster_bootstrap_mean(sums, counts, n_boot=400, seed=seed)
        hits += interval.lower <= 0 <= interval.upper
    assert 0.88 <= hits / runs <= 1.0


def test_single_event_gives_no_interval():
    interval, means = cluster_bootstrap_mean(np.array([0.3]), np.array([2.0]))
    assert np.isnan(interval.lower) and means.size == 0
    assert np.isnan(bootstrap_p_value(means))


def test_diebold_mariano_matches_corrected_t_test():
    d = np.array([-0.05, -0.02, 0.01, -0.08, -0.03, -0.01, 0.02, -0.06, -0.04, -0.02])
    statistic, p_value = diebold_mariano(d)
    n = len(d)
    t_stat, _ = stats.ttest_1samp(d, 0.0)
    assert statistic == pytest.approx(t_stat * np.sqrt((n - 1) / n))
    assert p_value == pytest.approx(2 * stats.t.sf(abs(statistic), df=n - 1))


def test_diebold_mariano_sign_flips_when_forecasters_swap():
    d = np.array([-0.05, -0.02, 0.01, -0.08, -0.03, -0.01])
    assert diebold_mariano(d)[0] == pytest.approx(-diebold_mariano(-d)[0])
    assert diebold_mariano(d)[1] == pytest.approx(diebold_mariano(-d)[1])


def test_diebold_mariano_degenerate_inputs():
    assert np.isnan(diebold_mariano(np.array([0.1, 0.2]))[0])  # fewer than 3 events
    assert np.isnan(diebold_mariano(np.array([0.1, 0.1, 0.1]))[0])  # no variation


# ---- on score tables -------------------------------------------------------------------------


def make_scored(n_events=12, contracts=3, seed=0) -> pd.DataFrame:
    """Forecaster 'good' is sharper than 'bad' on identical contracts."""
    rng = np.random.default_rng(seed)
    rows = []
    for event in range(n_events):
        for contract in range(contracts):
            outcome = int(rng.random() < 0.5)
            for name, noise in [("good", 0.1), ("bad", 0.45)]:
                p = float(np.clip(outcome * 0.8 + 0.1 + rng.normal(0, noise), 0.01, 0.99))
                rows.append(
                    {
                        "event_id": f"E{event}",
                        "market_ticker": f"E{event}-{contract}",
                        "forecaster": name,
                        "version": "1",
                        "mode": "backtest",
                        "probability": p,
                        "outcome": outcome,
                        "release_at": datetime(2025, 1, 1) + timedelta(days=event),
                        "made_at": datetime(2026, 1, 1),
                    }
                )
    return add_scores(pd.DataFrame(rows))


def test_compare_pair_detects_the_better_forecaster():
    scored = make_scored(n_events=40)
    result = compare_pair(scored, "good", "bad", "brier")
    assert result["n_events"] == 40 and result["n_contracts"] == 120
    assert result["mean_diff"] < 0 and result["ci_upper"] < 0
    assert result["dm_p_value"] < 0.05
    assert result["small_sample"] is False


def test_small_samples_are_flagged():
    assert compare_pair(make_scored(n_events=8), "good", "bad", "brier")["small_sample"]


def test_pairs_use_only_contracts_both_forecast():
    scored = make_scored(n_events=6)
    scored = scored[~((scored["forecaster"] == "bad") & (scored["event_id"] == "E0"))]
    assert compare_pair(scored, "good", "bad", "brier")["n_events"] == 5


def test_common_support_requires_every_forecaster():
    scored = make_scored(n_events=3)
    scored = scored[~((scored["forecaster"] == "bad") & (scored["market_ticker"] == "E0-0"))]
    shared = common_support(scored, ["good", "bad"])
    assert "E0-0" not in set(shared["market_ticker"])
    assert len(shared) == 2 * (3 * 3 - 1)


def test_leaderboard_has_intervals_and_uses_shared_contracts():
    board = leaderboard(make_scored(n_events=35))
    brier = board[board["metric"] == "brier"].set_index("forecaster")
    assert brier.loc["good", "mean_score"] < brier.loc["bad", "mean_score"]
    assert (brier["ci_lower"] <= brier["mean_score"]).all()
    assert (brier["mean_score"] <= brier["ci_upper"]).all()
    assert brier["n_contracts"].nunique() == 1


def test_comparison_table_flags_contaminated_llm_backtests():
    scored = make_scored(n_events=10)
    scored.loc[scored["forecaster"] == "bad", "forecaster"] = "llm"
    scored = add_scores(scored.drop(columns=["brier", "log_loss", "possibly_contaminated"]))
    table = comparisons(scored)
    assert table["possibly_contaminated"].all()


def test_latest_version_only():
    scored = make_scored(n_events=3)
    old = scored[scored["forecaster"] == "good"].copy()
    old["version"] = "0"
    old["made_at"] = datetime(2025, 1, 1)
    both = pd.concat([scored, old], ignore_index=True)
    kept = latest_versions(both)
    assert set(kept.loc[kept["forecaster"] == "good", "version"]) == {"1"}


def test_event_sums_orders_by_release():
    frame = pd.DataFrame(
        {
            "event_id": ["B", "A", "A"],
            "release_at": [datetime(2025, 2, 1), datetime(2025, 1, 1), datetime(2025, 1, 1)],
            "x": [1.0, 2.0, 3.0],
        }
    )
    sums, counts = event_sums(frame, "x")
    assert sums.tolist() == [5.0, 1.0] and counts.tolist() == [2.0, 1.0]


@pytest.fixture
def con(dbt_warehouse, tmp_path):
    copy = tmp_path / "warehouse.duckdb"
    shutil.copy(dbt_warehouse, copy)
    connection = duckdb.connect(str(copy))
    yield connection
    connection.close()


def test_tables_written_to_warehouse(con):
    run(con, ["base_rate", "market"], "backtest", datetime(2026, 10, 1))
    board_rows, pair_rows = write_comparisons(con)
    assert board_rows > 0 and pair_rows > 0
    row = con.execute(
        "select small_sample, n_events from eval.comparisons where metric = 'brier'"
    ).fetchone()
    assert row[0] is True  # the fixture has only a handful of events
