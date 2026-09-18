.PHONY: check format test hooks

hooks:
	uv run pre-commit install

check:
	uv run ruff check
	uv run mypy

format:
	uv run ruff format

test:
	uv run pytest
