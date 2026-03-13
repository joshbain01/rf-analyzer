# rf-monitor Makefile
# Common tasks for development, testing, and Raspberry Pi deployment.
#
# Usage:
#   make help            Show all targets
#   make install-dev     Install locally in editable mode with dev deps
#   make test            Run pytest suite
#   make lint            Check code style
#   make build           Build wheel distribution
#   make deploy PI=pi@192.168.1.100   Deploy to Pi
#   make clean           Remove build artifacts

.PHONY: help install-dev test lint build deploy clean

SHELL := /bin/bash
PYTHON := python3
PIP := pip
PI ?= pi@raspberrypi.local

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

build: clean ## Build wheel and sdist distributions
	$(PYTHON) -m build

# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

deploy: ## Deploy to Raspberry Pi (PI=user@host)
	@echo "Deploying to $(PI)..."
	bash deploy/deploy.sh $(PI)

deploy-sync: ## Sync files to Pi without running install (PI=user@host)
	bash deploy/deploy.sh $(PI) --sync-only

# ---------------------------------------------------------------------------
# Service Management (run on Pi via SSH)
# ---------------------------------------------------------------------------

pi-start: ## Start rf-monitor service on Pi (PI=user@host)
	ssh $(PI) "sudo systemctl start rf-monitor"

pi-stop: ## Stop rf-monitor service on Pi (PI=user@host)
	ssh $(PI) "sudo systemctl stop rf-monitor"

pi-restart: ## Restart rf-monitor service on Pi (PI=user@host)
	ssh $(PI) "sudo systemctl restart rf-monitor"

pi-status: ## Show rf-monitor service status on Pi (PI=user@host)
	ssh $(PI) "sudo systemctl status rf-monitor"

pi-logs: ## Tail rf-monitor service logs on Pi (PI=user@host)
	ssh -t $(PI) "sudo journalctl -u rf-monitor -f"

pi-scan: ## Run a one-off scan on Pi (PI=user@host)
	ssh -t $(PI) "sudo -u rf-monitor /opt/rf-monitor/venv/bin/rf-monitor scan -v"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean: ## Remove build artifacts, caches, and egg-info
	rm -rf build/ dist/ *.egg-info rf_monitor.egg-info/
	rm -rf .pytest_cache/ __pycache__/ rf_monitor/__pycache__/ tests/__pycache__/
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
