SHELL := /bin/bash
PATH := $(HOME)/.local/node/bin:$(PATH)

PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
RUFF ?= .venv/bin/ruff
MYPY ?= .venv/bin/mypy
LINT_IMPORTS ?= .venv/bin/lint-imports
NPM ?= npm

.PHONY: help install test lint typecheck dev-backend dev-frontend seed-demo docker-up clean

help:
	@echo "AI Talent Screener & Technical Interviewer — Workspace Commands"
	@echo "  make install       Install backend editable tools & frontend npm dependencies"
	@echo "  make test          Run full backend pytest (>=90% cov) + frontend vitest suite"
	@echo "  make lint          Run backend ruff + import-linter Clean Architecture contracts"
	@echo "  make typecheck     Run backend mypy --strict + frontend tsc --noEmit"
	@echo "  make seed-demo     Seed the Acme AI interactive demo dataset into DB_DSN"
	@echo "  make dev-backend   Launch ASGI/HTTP/SSE/WebSocket backend on :8000"
	@echo "  make dev-frontend  Launch Next.js 15 frontend Command Center on :3000"
	@echo "  make docker-up     Start multi-service production stack via docker-compose"

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	cd backend && ../$(PYTHON) -m pip install -e ".[dev]"
	cd frontend && $(NPM) install

lint:
	cd backend && ../$(RUFF) check src tests && PYTHONPATH=src ../$(LINT_IMPORTS)

typecheck:
	cd backend && ../$(MYPY) --strict src tests
	cd frontend && $(NPM) run typecheck

test:
	cd backend && PYTHONPATH=src ../$(PYTEST) --cov=talent_screener --cov-fail-under=90
	cd frontend && $(NPM) run coverage

seed-demo:
	cd backend && PYTHONPATH=src ../$(PYTHON) -m talent_screener.composition_root.main --seed-only

dev-backend:
	cd backend && PYTHONPATH=src ../$(PYTHON) -m talent_screener.composition_root.main --host $${API_HOST:-127.0.0.1} --port $${API_PORT:-8000}

dev-frontend:
	cd frontend && $(NPM) run dev

docker-up:
	docker compose -f docker-compose.production.yml up --build

clean:
	rm -rf backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache backend/.coverage frontend/.next frontend/coverage
