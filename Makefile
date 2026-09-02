SHELL := /bin/bash
UV := uv

.PHONY: deps setup build models start stop doctor test lint typecheck verify

deps:
	$(UV) sync --frozen --all-extras --group dev
setup: deps
	$(UV) run --frozen macbot setup
build:
	$(UV) run --frozen macbot build-inference --source "$(CURDIR)"
models:
	$(UV) run --frozen macbot models download qwen3.5-2b-official parakeet qwen3-tts-1.7b minilm silero
start:
	$(UV) run --frozen macbot start --background
stop:
	$(UV) run --frozen macbot stop
doctor:
	$(UV) run --frozen macbot doctor
test:
	$(UV) run --frozen --all-extras pytest -m 'not models and not device'
lint:
	$(UV) run --frozen ruff check src tests scripts
	$(UV) run --frozen ruff format --check src tests scripts
typecheck:
	$(UV) run --frozen mypy src/macbot
verify: lint typecheck test
	@echo "Local automated checks only; device, benchmark, package, audit, and listening gates are separate."
