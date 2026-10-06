# App image: runs ingestion, forecasting, evaluation, dbt and the Streamlit dashboard.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy packaging metadata first so dependency layers cache between code edits.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[app,dbt]"

COPY dbt_project ./dbt_project
COPY app ./app

EXPOSE 8501
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0"]
