.PHONY: install lint format test up down

install:
	pip install -e ".[dev,app,dbt]"

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .

test:
	pytest

up:
	docker compose up --build -d

down:
	docker compose down
