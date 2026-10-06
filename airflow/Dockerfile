# Airflow image with pmeval installed. dbt lives in its own virtualenv because its
# dependency pins can conflict with Airflow's constraint file.
FROM apache/airflow:2.10.5-python3.11

USER root
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

USER airflow
# DuckDB files written by a newer DuckDB cannot be opened by an older one. Python code and dbt run
# in separate environments here, so both are pinned to the same version.
ARG DUCKDB_VERSION=1.5.6

COPY --chown=airflow:root pyproject.toml README.md /opt/pmeval/
COPY --chown=airflow:root src /opt/pmeval/src
# The constraints file keeps Airflow's own dependencies at the versions it was tested with.
RUN pip install --no-cache-dir /opt/pmeval \
    --constraint https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.11.txt

RUN pip install --no-cache-dir "duckdb==${DUCKDB_VERSION}"

RUN python -m venv /home/airflow/dbt-venv \
    && /home/airflow/dbt-venv/bin/pip install --no-cache-dir "dbt-duckdb>=1.8" "duckdb==${DUCKDB_VERSION}"
