#!/bin/bash
# The project's completion standard (CLAUDE.md "验证").
set -e
cd "$(dirname "$0")/.."
uv sync --locked -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m pytest -q -p no:cacheprovider
