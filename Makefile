PYTHON ?= python3.14
VENV := backend/.venv
GUI := frontend
# The local Supabase database from `make db-up` (port in supabase/config.toml).
DATABASE_URL ?= postgresql://postgres:postgres@127.0.0.1:53322/postgres

.PHONY: setup backend backend-mock gui db-up db-reset test test-python test-gui eval eval-sourcing build doctor doctor-repair secret-scan

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --require-hashes -r backend/requirements.lock
	npm --prefix $(GUI) ci

backend:
	cd backend && DATABASE_URL="$(DATABASE_URL)" .venv/bin/python -m coworker.run

backend-mock:
	cd backend && DATABASE_URL="$(DATABASE_URL)" .venv/bin/python -m coworker.run --mock-provider

gui:
	npm --prefix $(GUI) run dev

db-up:
	supabase db start

db-reset:
	supabase db reset

test: test-python test-gui

test-python:
	cd backend && .venv/bin/pytest -q

test-gui:
	npm --prefix $(GUI) test

eval:
	cd backend && .venv/bin/python -m coworker.evals --artifacts .eval-artifacts

eval-sourcing:
	cd backend && .venv/bin/python -m coworker.evals --suite sourcing --artifacts .eval-artifacts

doctor:
	cd backend && .venv/bin/python -m coworker.doctor

doctor-repair:
	cd backend && .venv/bin/python -m coworker.doctor repair

# Confirms a registered secret's absence from local state without ever
# printing it. Pass KEY=<name> to scan for one value. After rotation, pass
# REVOKED_FROM=<pre-rotation-secrets.json> so the scan searches the revoked
# value rather than the replacement now in the live vault, e.g.
# `make secret-scan KEY=apollo REVOKED_FROM=./pre-rotation-secrets.json`.
secret-scan:
	cd backend && .venv/bin/python -m coworker.secret_scan $(if $(KEY),--secret-key $(KEY),) $(if $(REVOKED_FROM),--revoked-from $(REVOKED_FROM),)

build:
	npm --prefix $(GUI) run build
