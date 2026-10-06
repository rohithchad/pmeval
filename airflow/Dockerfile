# Airflow image with pmeval installed. dbt lives in its own virtualenv because its
# dependency pins can conflict with Airflow's constraint file.
FROM apache/airflow:2.10.5-python3.11

USER root
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

USER airflow
COPY --chown=airflow:root pyproject.toml README.md /opt/pmeval/
COPY --chown=airflow:root src /opt/pmeval/src
RUN pip install --no-cache-dir /opt/pmeval

RUN python -m venv /home/airflow/dbt-venv \
    && /home/airflow/dbt-venv/bin/pip install --no-cache-dir "dbt-duckdb>=1.8"
