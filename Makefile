# qatf — shortcuts for the commands in README.md and CLAUDE.md.
#
# Needs GNU make and a POSIX shell (Git Bash, WSL, macOS, Linux). Everything
# here also runs fine by hand; nothing is make-only.
#
# This file is a SHORTCUT, never a second source of truth. Every recipe is the
# same command the docs already give. If a target and the README disagree, the
# README is right and this file is the bug — the alternative is two places to
# read and one of them quietly wrong.

MODEL    ?= qwen3:14b
SERVICE  ?= qatf
BACKEND  := qatf-backend
FRONTEND := qatf-frontend
COMPOSE  := docker compose
DEV      := -f docker-compose.yaml -f docker-compose.dev.yaml

.DEFAULT_GOAL := help
.PHONY: help up down dev rebuild logs ps health         ollama vllm pull models         install install-backend install-frontend         check test test-backend test-frontend test-render lint typecheck clean

help:  ## list every target
	@grep -hE '^[a-z][a-z0-9-]*:.*##' $(MAKEFILE_LIST) 	  | sed 's/:.*##/|/' | sort | awk -F'|' '{printf "  %-17s %s
", $$1, $$2}'

# ---- running ---------------------------------------------------------------

up:  ## backend :8000 + web UI :3000
	$(COMPOSE) up -d

down:  ## stop the stack (leaves qatf-data/ and volumes untouched)
	$(COMPOSE) down

dev:  ## live reload — Vite HMR + uvicorn --reload, no image rebuild
	$(COMPOSE) $(DEV) up

rebuild:  ## rebuild + recreate one service (SERVICE=qatf)
	$(COMPOSE) up -d --build $(SERVICE)

logs:  ## follow logs (SERVICE=qatf)
	$(COMPOSE) logs -f $(SERVICE)

ps:  ## what is running
	$(COMPOSE) ps

health:  ## ffmpeg, provider, credential and GPU — check BEFORE submitting an hour of audio
	@curl -s http://localhost:8000/healthz

# ---- local models ----------------------------------------------------------

ollama:  ## start the local-model stack (uncomment its deploy: block for a GPU)
	$(COMPOSE) --profile ollama up -d

vllm:  ## start vLLM instead — guided decoding, so json_schema is real
	$(COMPOSE) --profile vllm up -d

pull:  ## pull a model into ollama (MODEL=qwen3:14b)
	$(COMPOSE) exec ollama ollama pull $(MODEL)

models:  ## list the models ollama holds
	$(COMPOSE) exec ollama ollama list

# ---- install ---------------------------------------------------------------

install: install-backend install-frontend  ## both halves

install-backend:  ## editable install with every provider extra (needs ffmpeg on PATH)
	cd $(BACKEND) && pip install -e ".[all]"

install-frontend:  ## npm ci
	cd $(FRONTEND) && npm ci

# ---- tests -----------------------------------------------------------------
# Everything under `check` runs without ffmpeg, a GPU, an API key or a network.

check: test lint typecheck  ## every offline gate — run this before pushing

test: test-backend test-frontend  ## the offline suites only

test-backend:  ## smoke_db + smoke_pipeline + smoke_llm + smoke_api + load_api
	cd $(BACKEND) && python tests/smoke_db.py
	cd $(BACKEND) && python tests/smoke_pipeline.py
	cd $(BACKEND) && python tests/smoke_llm.py
	cd $(BACKEND) && python tests/smoke_api.py
	cd $(BACKEND) && python tests/load_api.py

test-frontend:  ## vitest — the client-side mirrors of the server rules
	cd $(FRONTEND) && npm test

test-render:  ## NEEDS ffmpeg — renders clips and measures where the subject landed
	cd $(BACKEND) && python tests/verify_render.py

lint:  ## ruff
	cd $(BACKEND) && ruff check .

typecheck:  ## tsc --noEmit is the type gate
	cd $(FRONTEND) && npm run build

# ---- housekeeping ----------------------------------------------------------

clean:  ## remove Python caches. Never touches qatf-data/, media/ or node_modules/
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '*.pyc' -delete
