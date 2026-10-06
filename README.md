# Prediction Market Forecast Evaluation Platform

A data engineering pipeline that ingests Kalshi prediction-market data and official
US economic data, models it in a bronze/silver/gold DuckDB warehouse with dbt, produces
probability forecasts for recurring economic releases (CPI, jobs report, unemployment,
Fed rate decision, GDP) from the market price, a statistical model, an LLM and a
base-rate baseline, scores them against real outcomes, and shows the comparison on a
Streamlit dashboard.

Status: work in progress. See `docs/` once added.
