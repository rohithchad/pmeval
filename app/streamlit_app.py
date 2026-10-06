"""Prediction Market Forecast Evaluation dashboard.

Reads the DuckDB warehouse read-only. Run:  streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from pmeval import app_data

st.set_page_config(page_title="Forecast evaluation", layout="wide")

MODE_LABELS = {
    "live": "Live (predicted before the release)",
    "backtest": "Backtest (already-released events)",
}
METRIC_LABELS = {"brier": "Brier score", "log_loss": "Log loss"}


def color_scale(present) -> alt.Scale:
    """Fixed colour per forecaster (never repainted by filters), limited to those shown."""
    domain = [name for name in app_data.FORECASTER_ORDER if name in set(present)]
    return alt.Scale(domain=domain, range=[app_data.FORECASTER_COLORS[name] for name in domain])


DISCLAIMER = (
    "Not financial advice. This project evaluates forecast quality for research and learning. "
    "It does not recommend trades, and a forecaster that scores well here may still lose money "
    "after fees and spreads."
)
CONTAMINATION_WARNING = (
    "LLM backtests are possibly contaminated by training data: the model may have seen the "
    "outcome. Only predictions made live, before a release, are clean evidence of skill."
)


@st.cache_resource
def get_connection():
    """One read-only connection per server process."""
    return app_data.open_read_only()


def show_header() -> None:
    st.title("Who forecasts economic releases best?")
    st.caption(
        "Kalshi market price vs a statistical model vs an LLM vs a base rate, scored on "
        "CPI, jobs, unemployment, Fed decisions and GDP."
    )
    st.warning(DISCLAIMER)


def show_health(con) -> None:
    st.subheader("Data health")
    counts = app_data.counts(con)
    columns = st.columns(5)
    for column, (name, label) in zip(
        columns,
        [
            ("events", "Events"),
            ("resolved_events", "Resolved events"),
            ("live_predictions", "Live predictions"),
            ("backtest_predictions", "Backtest predictions"),
            ("scored_predictions", "Scored predictions"),
        ],
        strict=True,
    ):
        value = counts[name]
        column.metric(label, "no data yet" if value is None else f"{value:,}")
    health = app_data.data_health(con)
    if health.empty:
        st.info("No pipeline runs recorded yet. Runs appear here once the Airflow DAGs have run.")
        return
    health = health.rename(
        columns={
            "pipeline": "Pipeline",
            "step": "Step",
            "last_status": "Last status",
            "last_recorded_at": "Last run (UTC)",
            "last_success_at": "Last success (UTC)",
            "last_detail": "Detail",
        }
    )
    st.dataframe(health, hide_index=True, width="stretch")
    tests = health[health["Step"].isin(["dbt_test", "source_freshness"])]
    if tests.empty:
        st.caption("dbt test and freshness results appear after the weekly_quality DAG runs.")
    else:
        for _, row in tests.iterrows():
            icon = {"ok": "OK", "warn": "WARNING", "failed": "FAILED"}[row["Last status"]]
            st.write(f"**{row['Step']}**: {icon} ({row['Detail']})")


def interval_chart(board: pd.DataFrame, metric_label: str) -> alt.Chart:
    """Dot with a 95% interval whisker per forecaster. Lower is better."""
    base = alt.Chart(board).encode(
        y=alt.Y("label:N", sort=alt.SortField("mean_score"), title=None),
        color=alt.Color(
            "forecaster:N",
            scale=color_scale(board["forecaster"]),
            legend=alt.Legend(title="Forecaster"),
        ),
        tooltip=[
            alt.Tooltip("label:N", title="Forecaster"),
            alt.Tooltip("mean_score:Q", format=".4f", title=metric_label),
            alt.Tooltip("ci_lower:Q", format=".4f", title="95% interval low"),
            alt.Tooltip("ci_upper:Q", format=".4f", title="95% interval high"),
            alt.Tooltip("n_events:Q", title="Events"),
            alt.Tooltip("n_contracts:Q", title="Contracts"),
        ],
    )
    whisker = base.mark_rule(strokeWidth=2).encode(
        x=alt.X("ci_lower:Q", title=f"{metric_label} (lower is better)"), x2="ci_upper:Q"
    )
    dots = base.mark_circle(size=110, opacity=1).encode(x="mean_score:Q")
    return (whisker + dots).properties(height=40 * max(len(board), 2) + 40)


def show_leaderboard(con, mode: str, metric: str) -> None:
    st.subheader("Leaderboard")
    board = app_data.leaderboard(con, mode, metric)
    if board.empty:
        st.info(
            f"No {mode} results yet. Scores appear after predictions exist for events that have "
            "resolved. Results: [N] events, [N] contracts."
        )
        return
    if mode == "backtest" and (board["forecaster"] == "llm").any():
        st.error(CONTAMINATION_WARNING)
    if board["small_sample"].any():
        st.warning(
            "Small sample: some rows cover fewer than 30 events, so intervals and p-values are "
            "rough. A wide interval means we cannot tell forecasters apart."
        )
    st.altair_chart(interval_chart(board, METRIC_LABELS[metric]), width="stretch")
    table = board[
        ["label", "n_events", "n_contracts", "mean_score", "ci_lower", "ci_upper", "small_sample"]
    ].rename(
        columns={
            "label": "Forecaster",
            "n_events": "Events",
            "n_contracts": "Contracts",
            "mean_score": METRIC_LABELS[metric],
            "ci_lower": "95% low",
            "ci_upper": "95% high",
            "small_sample": "Small sample",
        }
    )
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption(
        "Intervals come from a cluster bootstrap that resamples whole events (contracts of one "
        "release are not independent). Forecasters are compared on the contracts they all forecast."
    )
    pairs = app_data.comparisons(con, mode, metric)
    if not pairs.empty:
        with st.expander("Pairwise differences (negative favours the first forecaster)"):
            shown = pairs[
                [
                    "forecaster_a",
                    "forecaster_b",
                    "n_events",
                    "mean_diff",
                    "ci_lower",
                    "ci_upper",
                    "bootstrap_p_value",
                    "dm_p_value",
                    "small_sample",
                ]
            ]
            st.dataframe(shown, hide_index=True, width="stretch")
            if mode == "backtest" and pairs["possibly_contaminated"].any():
                st.caption(f"Rows involving the LLM: {app_data.CONTAMINATION_NOTE}.")


def show_calibration(con, mode: str) -> None:
    st.subheader("Calibration")
    frame = app_data.calibration(con, mode)
    if frame.empty:
        st.info("No calibration data yet.")
        return
    diagonal = (
        alt.Chart(pd.DataFrame({"x": [0, 1], "y": [0, 1]}))
        .mark_line(strokeDash=[4, 4], color="#898781")
        .encode(x="x:Q", y="y:Q")
    )
    points = (
        alt.Chart(frame)
        .mark_line(point=alt.OverlayMarkDef(size=70), strokeWidth=2)
        .encode(
            x=alt.X(
                "mean_predicted:Q", title="Forecast probability", scale=alt.Scale(domain=[0, 1])
            ),
            y=alt.Y(
                "observed_frequency:Q",
                title="Share that resolved YES",
                scale=alt.Scale(domain=[0, 1]),
            ),
            color=alt.Color(
                "forecaster:N",
                scale=color_scale(frame["forecaster"]),
                legend=alt.Legend(title="Forecaster"),
            ),
            tooltip=[
                alt.Tooltip("label:N", title="Forecaster"),
                alt.Tooltip("mean_predicted:Q", format=".2f", title="Forecast"),
                alt.Tooltip("observed_frequency:Q", format=".2f", title="Observed"),
                alt.Tooltip("count:Q", title="Contracts in bin"),
            ],
        )
    )
    st.altair_chart((diagonal + points).properties(height=380), width="stretch")
    st.caption(
        "Points on the dashed diagonal are perfectly calibrated. Bins with few contracts are "
        "noisy; hover to see the count."
    )
    if mode == "backtest" and (frame["forecaster"] == "llm").any():
        st.error(CONTAMINATION_WARNING)


def show_event_drilldown(con) -> None:
    st.subheader("Event drill-down")
    events = app_data.events(con)
    if events.empty:
        st.info("No events yet.")
        return
    labels = {
        row.event_id: f"{row.event_id}  ({row.series_key}, release {row.release_at:%Y-%m-%d})"
        for row in events.itertuples()
    }
    event_id = st.selectbox("Event", list(labels), format_func=labels.get)
    info = events[events["event_id"] == event_id].iloc[0]
    st.write(
        f"**Forecast time:** {info['forecast_time']} UTC  |  "
        f"**Release:** {info['release_at']} UTC  |  "
        f"**Release time source:** {info['release_time_source']}"
    )
    contracts = app_data.event_contracts(con, event_id)
    if contracts.empty:
        st.info("No contract data for this event.")
        return
    st.dataframe(contracts, hide_index=True, width="stretch")
    if any(" (backtest)" in c and c.startswith("llm") for c in contracts.columns):
        st.error(CONTAMINATION_WARNING)


def show_prediction_log(con) -> None:
    st.subheader("Prediction log")
    log = app_data.prediction_log(con)
    if log.empty:
        st.info("No predictions stored yet.")
        return
    modes = st.multiselect("Mode", ["live", "backtest"], default=["live", "backtest"])
    forecasters = st.multiselect(
        "Forecaster", sorted(log["forecaster"].unique()), default=sorted(log["forecaster"].unique())
    )
    shown = log[log["mode"].isin(modes) & log["forecaster"].isin(forecasters)]
    st.dataframe(shown, hide_index=True, width="stretch")
    st.caption(
        "made_at is when the prediction was produced. Live predictions are made before the "
        "release; backtests are produced afterwards using only data available at forecast_time."
    )


def main() -> None:
    show_header()
    con = get_connection()
    show_health(con)
    st.divider()
    left, right = st.columns(2)
    mode = left.radio(
        "Predictions", list(MODE_LABELS), format_func=MODE_LABELS.get, horizontal=True
    )
    metric = right.radio(
        "Metric", list(METRIC_LABELS), format_func=METRIC_LABELS.get, horizontal=True
    )
    show_leaderboard(con, mode, metric)
    show_calibration(con, mode)
    st.divider()
    show_event_drilldown(con)
    st.divider()
    show_prediction_log(con)
    st.divider()
    st.caption(DISCLAIMER)


main()
