.PHONY: check format test

check:
	uv run ruff check
	uv run mypy

format:
	uv run ruff format

test:
	uv run pytest
