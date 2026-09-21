.PHONY: check format test test-integration hooks

hooks:
	uv run pre-commit install

check:
	uv run ruff check
	uv run mypy

format:
	uv run ruff format

test:
	uv run pytest -m "not integration"

test-integration:
	uv run pytest -m integration