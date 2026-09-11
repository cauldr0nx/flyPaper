# Local CI. There is deliberately no GitHub Actions workflow yet; `make ci` is the gate.
.PHONY: ci lint fmt test install clean dashboard dashboard-data

install:
	uv pip install -e ".[dev]"

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

ci: lint test

dashboard-data:
	uv run python -m flypaper.web.export

dashboard:
	uv run python -m flypaper.web.server --tailscale

clean:
	rm -rf .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
