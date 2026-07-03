.PHONY: help install install-dev install-all test run docker-up docker-down clean

help:
	@echo "UMLS CDSS Makefile"
	@echo ""
	@echo "Available commands:"
	@echo "  make install      - Install dependencies (Poetry)"
	@echo "  make install-dev  - Install with dev dependencies"
	@echo "  make install-all  - Install with all groups (dev + experiments)"
	@echo "  make test         - Run tests"
	@echo "  make run          - Run API server"
	@echo "  make docker-up    - Start Docker services"
	@echo "  make docker-down  - Stop Docker services"
	@echo "  make clean        - Clean temporary files"
	@echo "  make export-req   - Export requirements.txt from Poetry"

install:
	poetry install --no-dev

install-dev:
	poetry install

install-all:
	poetry install --with dev,experiments

test:
	poetry run pytest tests/ -v

run:
	poetry run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

docker-up:
	cd infrastructure/docker && docker-compose up -d

docker-down:
	cd infrastructure/docker && docker-compose down

docker-logs:
	cd infrastructure/docker && docker-compose logs -f

clean:
	find . -type d -name __pycache__ -exec rm -r {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name "*.egg-info" -exec rm -r {} +
	rm -rf .pytest_cache
	rm -rf .mypy_cache

db-init:
	psql -U postgres -d umls_cdss -f etl/sql/create_tables.sql
	psql -U postgres -d umls_cdss -f etl/sql/pgvector_init.sql
	psql -U postgres -d umls_cdss -f etl/sql/index_optimizations.sql

format:
	poetry run black api/ etl/ tests/
	poetry run isort api/ etl/ tests/

lint:
	poetry run flake8 api/ etl/ tests/
	poetry run mypy api/

export-req:
	poetry export -f requirements.txt --output requirements.txt --without-hashes
	poetry export -f requirements.txt --output requirements-dev.txt --with dev --without-hashes

