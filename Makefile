PYTHON ?= python3.14
VENV := backend/.venv
GUI := frontend
ifeq ($(OS),Windows_NT)
SHELL := C:/Progra~1/Git/bin/bash.exe
VENV_BINDIR := Scripts
else
VENV_BINDIR := bin
endif
SUPABASE ?= npx --no-install supabase
# The local Supabase database from `make db-up` (port in supabase/config.toml).
DATABASE_URL ?= postgresql://postgres:postgres@127.0.0.1:53322/postgres

.PHONY: check check-1 check-2 check-3 check-4 check-5 check-6 setup backend backend-mock gui native db-up db-reset test test-python test-gui eval eval-sourcing build doctor doctor-repair secret-scan build-backend smoke-test test-update update-status update-rollback update-manifest update-verify

setup:
	npm ci
	$(PYTHON) -m venv $(VENV)
	$(VENV)/$(VENV_BINDIR)/python -m pip install --require-hashes -r backend/requirements.lock
	npm --prefix $(GUI) ci

backend:
	cd backend && DATABASE_URL="$(DATABASE_URL)" .venv/$(VENV_BINDIR)/python -m coworker.run

backend-mock:
	cd backend && DATABASE_URL="$(DATABASE_URL)" .venv/$(VENV_BINDIR)/python -m coworker.run --mock-provider

gui:
	npm --prefix $(GUI) run dev

native:
	npm --prefix $(GUI) run tauri:dev

db-up:
	$(SUPABASE) db start

db-reset:
	$(SUPABASE) db reset

test: test-python test-gui

test-python:
	cd backend && .venv/$(VENV_BINDIR)/python -m pytest -q

test-gui:
	npm --prefix $(GUI) test

# --- starter checks ---------------------------------------------------------
# One command for each piece that you write: `make check-1` to `make check-6`.
# The last line is the answer: PIECE N: PASS, or PIECE N: NOT YET.
# `make check` shows all six. Checks 1, 2, 4 and 5 need `make db-up` first.
# Check 3 needs Docker. Do the pieces in order: a check needs the pieces before it.

PIECE_TEST := cd backend && .venv/$(VENV_BINDIR)/python -m pytest -q -x --tb=short
verdict = && printf '\nPIECE $(1): PASS\n' || printf '\nPIECE $(1): NOT YET. The first failure above tells you what is wrong.\n'

# Piece 1: Database.tx and _service_user in backend/coworker/db.py
check-1:
	@( $(PIECE_TEST) tests/test_db.py tests/test_hosted_demo.py::test_resolve_returns_workspace_user_id_when_set ) $(call verdict,1)

# Piece 2: the table meeting_candidates in supabase/migrations/20261110000800_meeting_evidence.sql
check-2:
	@( $(PIECE_TEST) tests/test_meeting_evidence.py tests/test_row_level_security.py ) $(call verdict,2)

# Piece 3: backend/Dockerfile
check-3:
	@( sh backend/tests/check_container.sh ) $(call verdict,3)

# Piece 4: /healthz in backend/coworker/server.py, and the public-address rule in backend/coworker/run.py
check-4:
	@( $(PIECE_TEST) tests/test_hosted_demo.py -k "healthz or public_host or launcher" ) $(call verdict,4)

# Piece 5: HostedAuth.check in backend/coworker/hosted_auth.py
check-5:
	@( $(PIECE_TEST) tests/test_hosted_demo.py ) $(call verdict,5)

# Piece 6: frontend/src/auth.ts and frontend/src/api.ts
check-6:
	@( cd $(GUI) && npx vitest run --config vitest.config.ts tests/authGate.test.tsx && npx tsc --noEmit ) $(call verdict,6)

check:
	@for n in 1 2 3 4 5 6; do \
		if "$(MAKE)" -s check-$$n 2>/dev/null | grep -q "^PIECE $$n: PASS"; then echo "PIECE $$n: PASS"; else echo "PIECE $$n: NOT YET"; fi; \
	done

eval:
	cd backend && .venv/$(VENV_BINDIR)/python -m coworker.evals --artifacts .eval-artifacts

eval-sourcing:
	cd backend && .venv/$(VENV_BINDIR)/python -m coworker.evals --suite sourcing --artifacts .eval-artifacts

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

build-backend:
	PYTHON="$(PYTHON)" bash backend/packaging/build_backend.sh

smoke-test:
	$(VENV)/bin/python backend/packaging/smoke_test.py frontend/src-tauri/resources/sourcecado-backend/sourcecado-backend

# --- preview update channel -------------------------------------------------
# docs/engineering/update-channel.md explains every target below.

test-update:
	cd backend && .venv/bin/pytest -q tests/test_update_manifest.py tests/test_update_drain.py \
		tests/test_update_apply.py tests/test_update_exercises.py \
		tests/test_update_secrets.py tests/test_update_health.py \
		tests/test_update_channel_mutations.py

# Is anything in flight that an update would interrupt? Reads only.
update-status:
	cd backend && .venv/bin/python -m coworker.update_channel status

# BUNDLE is the installed app. BACKUP is optional: omit it when the failed
# update never reached the migration step. Quit Sourcecado first.
update-rollback:
	cd backend && .venv/bin/python -m coworker.update_channel rollback \
		--bundle "$(BUNDLE)" $(if $(BACKUP),--backup "$(BACKUP)",) $(if $(LIST),--list-backups,)

# Needs SOURCECADO_UPDATE_SIGNING_KEY in the environment. Refuses to write a
# manifest whose build metadata matches the credential scan.
update-manifest:
	cd backend && .venv/bin/python packaging/update_manifest.py generate \
		--artifact "$(ARTIFACT)" --output "$(MANIFEST)" --version "$(VERSION)" \
		--minimum-from "$(MINIMUM_FROM)" --key-id "$(KEY_ID)" \
		--commit "$$(git rev-parse HEAD)" \
		--released-at "$$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Needs only the public key, the same one every installation would hold.
update-verify:
	cd backend && .venv/bin/python packaging/update_manifest.py verify \
		--manifest "$(MANIFEST)" --artifact "$(ARTIFACT)" \
		--installed-version "$(INSTALLED_VERSION)" \
		--trust "$(KEY_ID)=$(PUBLIC_KEY)"
