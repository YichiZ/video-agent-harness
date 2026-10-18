.PHONY: help sync test test-live test-web web lint fmt media doctor chat serve

help:  ## list targets
	@grep -E "^[a-z-]+:.*##" $(MAKEFILE_LIST) | sed -E "s/:.*## /\t/"

sync:  ## install dependencies
	uv sync

test: media  ## unit + offline e2e tests (no keys needed)
	uv run pytest

test-live: media  ## live tests against the real APIs; add ARGS=--record to refresh fixtures
	uv run pytest -m live $(ARGS)

test-web:  ## UI type check, vitest and build (web/dist, which `serve` and the browser tests use)
	cd web && npx vitest run && npm run build

web:  ## build the UI into web/dist
	cd web && npm run build

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

serve:  ## run the HTTP server on localhost:8000 (step 10)
	uv run python -m server.cli serve
