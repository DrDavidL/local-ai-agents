#!/usr/bin/env bash
# Pre-push hook: runs ruff, pip-audit, and tests before allowing a push.
# Install: ln -sf ../../scripts/pre-push.sh .git/hooks/pre-push

set -euo pipefail

# Ensure dev tools are available
uv sync --extra dev --quiet

echo "==> Running ruff check..."
uv run ruff check .

echo "==> Running pip-audit..."
uv run pip-audit

echo "==> Running tests..."
uv run pytest tests/ -q

echo "==> All checks passed."
