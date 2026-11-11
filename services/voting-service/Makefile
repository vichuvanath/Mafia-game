.PHONY: help install lint format typecheck test-unit test-integration test coverage run docker-build docker-up docker-down

help:
	@echo "Available commands:"
	@echo "  make install           - Install development dependencies"
	@echo "  make lint              - Run ruff linter and format check"
	@echo "  make format            - Format code with ruff"
	@echo "  make typecheck         - Run mypy type checking"
	@echo "  make test-unit         - Run unit tests without I/O"
	@echo "  make test-integration  - Run integration tests (requires docker or testcontainers)"
	@echo "  make test              - Run all tests"
	@echo "  make coverage          - Run tests with coverage report"
	@echo "  make run               - Run service locally with uvicorn"
	@echo "  make docker-up         - Start local test containers (Postgres, Redis, RabbitMQ)"
	@echo "  make docker-down       - Stop local test containers"

install:
	pip install --upgrade pip
	pip install -r requirements-dev.txt

lint:
	ruff check app tests
	ruff format --check app tests
	mypy app/core --strict
	mypy app

format:
	ruff check --fix app tests
	ruff format app tests

typecheck:
	mypy app/core --strict
	mypy app

test-unit:
	pytest tests/unit -v

test-integration:
	pytest tests/integration -v

test:
	pytest -v

coverage:
	pytest --cov=app --cov-report=term-missing --cov-report=html:htmlcov --cov-fail-under=80 -v
	pytest tests/unit --cov=app/core --cov-report=term-missing --cov-fail-under=90 -v

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

docker-up:
	docker compose -f docker-compose.test.yml up -d

docker-down:
	docker compose -f docker-compose.test.yml down -v

