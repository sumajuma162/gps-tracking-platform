# Linux/macOS/WSL helpers. Windows PowerShell equivalents live in scripts/dev.ps1.
.PHONY: backup monitoring load-test up down logs ps test test-gateway test-backend test-frontend lint
up:            ; docker compose up -d --build
down:          ; docker compose down
logs:          ; docker compose logs -f --tail=100
ps:            ; docker compose ps
test: test-gateway test-backend test-frontend
test-gateway:  ; cd gateway && pytest -q
test-backend:  ; cd backend && TEST_SQLITE=1 pytest -q
test-frontend: ; cd frontend && npm run lint && npm test
lint:          ; cd gateway && ruff check . && mypy; cd backend && ruff check .
seed:          ; docker compose exec backend python manage.py seed_demo
superadmin:    ; docker compose exec backend python manage.py create_superadmin $(EMAIL)
backup:        ; mkdir -p backups && docker compose exec -T postgres sh -c 'pg_dump -U $$POSTGRES_USER -d $$POSTGRES_DB -Fc' > backups/gps_$$(date -u +%Y%m%dT%H%M%SZ).dump
monitoring:    ; docker compose --profile monitoring up -d prometheus grafana
load-test:     ; PYTHONPATH=gateway:simulator python -m gpssim.load --devices $${DEVICES:-1000} --interval 15 --records-per-packet 3 --duration 120 --warmup 30
e2e:           ; E2E_BASE_URL=http://localhost pytest e2e -q
