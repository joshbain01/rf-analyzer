# rf-monitor Makefile
# Common tasks for development, testing, and container operations.

.PHONY: help install-dev test test-cov lint typecheck build docker-build docker-up docker-down docker-logs docker-restart clean

SHELL := /bin/bash
PYTHON := python3
PIP := pip

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install-dev: ## Install in editable mode with dev dependencies
	$(PIP) install -e ".[dev]"

test: ## Run all tests with pytest
	$(PYTHON) -m pytest tests/ -v --tb=short

test-cov: ## Run tests with coverage report
	$(PYTHON) -m pytest tests/ -v --tb=short --cov=rf_monitor --cov-report=term-missing

lint: ## Check code with flake8 (install separately: pip install flake8)
	$(PYTHON) -m flake8 rf_monitor/ tests/ --max-line-length=120 --ignore=E501,W503

typecheck: ## Run mypy type checking (install separately: pip install mypy)
	$(PYTHON) -m mypy rf_monitor/ --ignore-missing-imports

build: clean ## Build wheel and sdist distributions
	$(PYTHON) -m build

docker-build: ## Build the runtime image
	docker compose build

docker-up: ## Start rf-monitor via Docker Compose
	docker compose up -d

docker-down: ## Stop rf-monitor and remove containers
	docker compose down

docker-logs: ## Tail container logs
	docker compose logs -f

docker-restart: ## Restart rf-monitor service
	docker compose restart

clean: ## Remove build artifacts, caches, and egg-info
	rm -rf build/ dist/ *.egg-info rf_monitor.egg-info/
	rm -rf .pytest_cache/ __pycache__/ rf_monitor/__pycache__/ tests/__pycache__/
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
