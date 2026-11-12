VENV ?= .venv
PYTHON = $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
PIP = $(if $(wildcard $(VENV)/bin/pip),$(VENV)/bin/pip,pip3)
PYTEST = $(if $(wildcard $(VENV)/bin/pytest),$(VENV)/bin/pytest,pytest)
RUFF = $(if $(wildcard $(VENV)/bin/ruff),$(VENV)/bin/ruff,ruff)
MYPY = $(if $(wildcard $(VENV)/bin/mypy),$(VENV)/bin/mypy,mypy)
UVICORN = $(if $(wildcard $(VENV)/bin/uvicorn),$(VENV)/bin/uvicorn,uvicorn)

.PHONY: help install lint format test-unit test-integration test coverage run clean

help:
	@echo "Available commands:"
	@echo "  make install           - Install production and dev dependencies"
	@echo "  make lint              - Run ruff linter, formatting checks, and mypy (strict on app/engine)"
	@echo "  make format            - Run ruff code formatting and auto-fix"
	@echo "  make test-unit         - Run fast in-memory unit tests"
	@echo "  make test-integration  - Run integration tests (testcontainers or local infra)"
	@echo "  make test              - Run all tests"
	@echo "  make coverage          - Run tests with code coverage report"
	@echo "  make run               - Run local development server"
	@echo "  make clean             - Remove cache files and build artifacts"

install:
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

lint:
	$(RUFF) check .
	$(RUFF) format --check .
	$(MYPY) --strict app/engine
	$(MYPY) app

format:
	$(RUFF) format .
	$(RUFF) check --fix .

test-unit:
	$(PYTEST) tests/unit

test-integration:
	$(PYTEST) tests/integration

test:
	$(PYTEST) tests

coverage:
	$(PYTEST) --cov=app --cov-report=term-missing --cov-report=html:htmlcov

run:
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --reload

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name "__pycache__" -exec rm -rf {} +
