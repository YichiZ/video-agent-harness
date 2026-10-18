.PHONY: help sync test test-live lint fmt media doctor chat eval

help:  ## list targets
	@grep -E "^[a-z-]+:.*##" $(MAKEFILE_LIST) | sed -E "s/:.*## /\t/"

sync:  ## install dependencies
	uv sync

test: media  ## unit + offline e2e tests (no keys needed)
	uv run pytest

test-live: media  ## live tests against the real APIs; add ARGS=--record to refresh fixtures
	uv run pytest -m live $(ARGS)

lint:  ## ruff lint and format check
	uv run ruff check .
	uv run ruff format --check .

fmt:  ## apply ruff fixes and formatting
	uv run ruff check --fix .
	uv run ruff format .

media:  ## build generated test media into tests/media/
	scripts/make_test_media.sh

doctor:  ## check tools, config, keys and Claude auth
	uv run python -m server.cli doctor

chat:  ## chat about a video (step 5): make chat VIDEO=path/to/video.mp4
	uv run python -m server.cli chat $(VIDEO)

SUBSET ?= smoke
eval:  ## compare prompt v1 with v2 (step 12): make eval SUBSET=smoke
	@test "$(SUBSET)" = smoke || { echo "only SUBSET=smoke exists until step 13"; exit 2; }
	uv run python -m evals.smoke
