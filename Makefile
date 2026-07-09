.PHONY: install db-up db-down run test lint format

install:
	python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -r backend/requirements.txt

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

run:
	cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000

test:
	cd backend && ../.venv/bin/pytest -v

lint:
	.venv/bin/ruff check backend

format:
	.venv/bin/ruff format backend

seed:
	PYTHONPATH=backend .venv/bin/python scripts/generate_data.py


eval:
	PYTHONPATH=backend .venv/bin/python -m app.evaluation.runner
