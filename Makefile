.PHONY: up down logs build migrate test test-unit shell clickhouse-client clean

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build

migrate:
	docker compose exec api alembic upgrade head

test:
	docker compose exec api pytest tests/ -v --tb=short --cov=src --cov-report=term-missing

test-unit:
	docker compose exec api pytest tests/unit/ -v --tb=short

shell:
	docker compose exec api python

clickhouse-client:
	docker compose exec clickhouse clickhouse-client --database adpilot

clean:
	docker compose down -v --remove-orphans
