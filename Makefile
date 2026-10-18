.PHONY: help sync test test-live lint fmt media doctor chat

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
