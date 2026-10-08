# Every recipe is phony (none produce a file named after the target) (#796).
.PHONY: help install install-dev lock update-deps test test-unit test-integration test-fast \
	test-ci test-perf test-coverage test-coverage-min test-watch lint lint-fix format typecheck security \
	security-full docker-build docker-up docker-down docker-down-clean docker-logs docker-ps \
	docker-shell docker-test pre-commit pre-commit-update ci ci-docker clean clean-all dev \
	serve stop restart shell notebook db-migrate db-reset logs metrics prometheus docs \
	docs-coverage benchmark quick-test quick-check quick-commit show-config tree size \
	profile models

# Default target
.DEFAULT_GOAL := help

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m  # No Color

# Compose v2 plugin syntax; override with `make COMPOSE=docker-compose ...`.
COMPOSE ?= docker compose
# The service defined in docker-compose.yml (#322).
APP_SERVICE ?= scraper
# Cross-platform "open this": Linux `xdg-open`, macOS `open`, else just print (#339).
OPEN := $(shell command -v xdg-open 2>/dev/null || command -v open 2>/dev/null || echo echo)

##@ General

help: ## Display this help message
	@echo "$(BLUE)Scraping Pipeline - Development Commands$(NC)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "Usage:\n  make $(YELLOW)<target>$(NC)\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  $(GREEN)%-20s$(NC) %s\n", $$1, $$2 } /^##@/ { printf "\n$(BLUE)%s$(NC)\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Installation

install: ## Install production dependencies
	@echo "$(BLUE)Installing production dependencies...$(NC)"
	pip install --upgrade pip pip-tools
	pip install -r requirements.txt

install-dev: install ## Install development dependencies
	@echo "$(BLUE)Installing development dependencies...$(NC)"
	pip install -r dev-requirements.txt
	@# The git hook runs from the repo root, so point it at this directory's config (#358).
	pre-commit install --config "$$(git rev-parse --show-prefix).pre-commit-config.yaml"
	@echo "$(GREEN)✓ Development environment ready!$(NC)"

# Lockfiles are compiled from this directory with relative paths so the generated
# header never embeds a machine-local path (#397). See ../CONTRIBUTING.md.
PIP_COMPILE = pip-compile --quiet --strip-extras

lock: ## Re-pin lockfiles after editing *.in (keeps existing versions)
	@echo "$(BLUE)Compiling lockfiles (no upgrades)...$(NC)"
	pip install --upgrade pip-tools
	cd "$(CURDIR)" && $(PIP_COMPILE) --output-file=requirements.txt requirements.in
	cd "$(CURDIR)" && $(PIP_COMPILE) --output-file=dev-requirements.txt dev-requirements.in
	@echo "$(GREEN)✓ Lockfiles compiled$(NC)"

update-deps: ## Upgrade every pin and recompile lockfiles
	@echo "$(BLUE)Updating dependencies...$(NC)"
	pip install --upgrade pip pip-tools
	cd "$(CURDIR)" && $(PIP_COMPILE) --upgrade --output-file=requirements.txt requirements.in
	cd "$(CURDIR)" && $(PIP_COMPILE) --upgrade --output-file=dev-requirements.txt dev-requirements.in
	@echo "$(GREEN)✓ Dependencies updated$(NC)"

##@ Testing

test: ## Run all tests
	@echo "$(BLUE)Running all tests...$(NC)"
	pytest -v

test-unit: ## Run unit tests only
	@echo "$(BLUE)Running unit tests...$(NC)"
	pytest tests/unit -v

test-integration: ## Run integration tests (requires services)
	@echo "$(BLUE)Running integration tests...$(NC)"
	pytest tests/integration -v

test-fast: ## Run fast tests only (excluding slow and performance)
	@echo "$(BLUE)Running fast tests...$(NC)"
	pytest -v -m "not slow and not performance"

test-ci: ## Run the default PR selection exactly as CI does
	@echo "$(BLUE)Running CI default test selection...$(NC)"
	python -m pytest tests/ -m "not slow and not kafka and not performance" -o addopts= --strict-markers

test-smoke: ## Fast offline smoke gate (imports, one spider parse, one pipeline, settings; #286)
	@echo "$(BLUE)Running smoke tests...$(NC)"
	python -m pytest tests/ -m smoke -o addopts= -q

profile: ## Profile one CLI run: make profile CMD="pipeline --skip-stage1" (writes data/exports/profiles/, #465)
	python cli.py --profile $(CMD)

models: ## Download pinned ML models and verify checksums (models.lock.json, #486)
	python cli.py setup

test-perf: ## Run load/performance tests (excluded from PR CI, #287)
	@echo "$(BLUE)Running performance tests...$(NC)"
	python -m pytest tests/performance -m performance -o addopts= -v

test-coverage: ## Run tests with coverage report
	@echo "$(BLUE)Running tests with coverage...$(NC)"
	pytest --cov=src --cov-report=html --cov-report=term-missing
	@echo "$(GREEN)✓ Coverage report generated at htmlcov/index.html$(NC)"

test-coverage-min: ## Check minimum coverage threshold
	@echo "$(BLUE)Checking coverage threshold...$(NC)"
	pytest --cov=src --cov-fail-under=70

test-watch: ## Run tests in watch mode (needs `pip install pytest-watch`)
	@command -v ptw >/dev/null 2>&1 || { echo "$(YELLOW)pytest-watch is not installed: pip install pytest-watch$(NC)"; exit 1; }
	@echo "$(BLUE)Running tests in watch mode...$(NC)"
	ptw -- -o addopts=

##@ Code Quality

# ruff covers the pyflakes/pycodestyle checks flake8 ran; flake8 had no config
# and duplicated (and disagreed with) ruff, so it is no longer part of lint (#361).
# Same single path as the pre-commit hooks (#357): ruff lint (incl. isort "I") + ruff format.
lint: ## Run linters (ruff check + ruff format --check), same as pre-commit
	@echo "$(BLUE)Running linters...$(NC)"
	ruff check .
	ruff format --check src tests
	@echo "$(GREEN)✓ Linting complete$(NC)"

lint-fix: ## Fix linting issues automatically (ruff --fix + ruff format)
	@echo "$(BLUE)Fixing linting issues...$(NC)"
	ruff check --fix .
	ruff format src tests
	@echo "$(GREEN)✓ Auto-fix complete$(NC)"

format: lint-fix ## Format code (alias for lint-fix)

typecheck: ## Run type checking with mypy
	@echo "$(BLUE)Running type checker...$(NC)"
	mypy src --ignore-missing-imports

##@ Security

security: ## Run security checks
	@echo "$(BLUE)Running security scans...$(NC)"
	pip-audit --desc
	bandit -r src -ll
	@echo "$(GREEN)✓ Security scan complete$(NC)"

security-full: ## Run comprehensive security scan
	@echo "$(BLUE)Running comprehensive security scan...$(NC)"
	pip-audit --desc --skip-editable
	bandit -r src -f json -o bandit-report.json
	trivy fs --security-checks vuln,config .
	@echo "$(GREEN)✓ Full security scan complete$(NC)"

##@ Docker

docker-build: ## Build Docker images
	@echo "$(BLUE)Building Docker images...$(NC)"
	$(COMPOSE) build --parallel

docker-up: ## Start Docker services
	@echo "$(BLUE)Starting Docker services...$(NC)"
	$(COMPOSE) up -d
	@echo "$(GREEN)✓ Services started$(NC)"
	@echo "Run 'make docker-logs' to view logs"

docker-down: ## Stop Docker services
	@echo "$(BLUE)Stopping Docker services...$(NC)"
	$(COMPOSE) down
	@echo "$(GREEN)✓ Services stopped$(NC)"

docker-down-clean: ## Stop and remove volumes
	@echo "$(YELLOW)Stopping services and removing volumes...$(NC)"
	$(COMPOSE) down -v
	@echo "$(GREEN)✓ Services and volumes removed$(NC)"

docker-logs: ## View Docker logs
	$(COMPOSE) logs -f

docker-ps: ## Show running containers
	$(COMPOSE) ps

docker-shell: ## Open shell in scrapy container
	$(COMPOSE) exec $(APP_SERVICE) /bin/bash

docker-test: docker-up ## Run tests in Docker
	@echo "$(BLUE)Running tests in Docker...$(NC)"
	@sleep 5  # Wait for services to be ready
	$(COMPOSE) exec -T $(APP_SERVICE) pytest -v
	@make docker-down

##@ Pre-commit

pre-commit: ## Run pre-commit hooks on all files
	@echo "$(BLUE)Running pre-commit hooks...$(NC)"
	pre-commit run --all-files

pre-commit-update: ## Update pre-commit hooks
	@echo "$(BLUE)Updating pre-commit hooks...$(NC)"
	pre-commit autoupdate

##@ CI/CD

ci: ## Run all CI checks locally
	@echo "$(BLUE)Running CI pipeline locally...$(NC)"
	@make lint
	@make typecheck
	@make security
	@make test-coverage-min
	@echo "$(GREEN)✓ All CI checks passed!$(NC)"

ci-docker: ## Run CI checks in Docker
	@echo "$(BLUE)Running CI pipeline in Docker...$(NC)"
	@make docker-build
	@make docker-test
	@echo "$(GREEN)✓ Docker CI checks passed!$(NC)"

##@ Cleanup

clean: ## Clean up generated files
	@echo "$(BLUE)Cleaning up...$(NC)"
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .coverage htmlcov/ coverage.xml
	rm -rf dist/ build/
	@echo "$(GREEN)✓ Cleanup complete$(NC)"

clean-all: clean docker-down-clean ## Deep clean (including Docker volumes)
	@echo "$(YELLOW)Performing deep clean...$(NC)"
	rm -rf data/test_delta_lake/
	rm -rf .venv/
	@echo "$(GREEN)✓ Deep clean complete$(NC)"

##@ Development

dev: install-dev docker-up ## Setup complete development environment
	@echo "$(GREEN)✓ Development environment ready!$(NC)"
	@echo "Services running:"
	@make docker-ps

serve: docker-up ## Start all services
	@echo "$(GREEN)Services are running$(NC)"
	@echo "Grafana: http://localhost:3000"
	@echo "Prometheus: http://localhost:9090"

stop: docker-down ## Stop all services

restart: docker-down docker-up ## Restart all services

shell: ## Open Python shell with project context
	python -i -c "import sys; sys.path.insert(0, 'src')"

notebook: ## Start Jupyter notebook
	jupyter notebook

##@ Database

db-migrate: ## Create/upgrade the Postgres metrics schema (idempotent CREATE IF NOT EXISTS)
	@echo "$(BLUE)Initializing database schema...$(NC)"
	python -c "from src.utils.postgres import PostgresManager; m = PostgresManager.get_instance(); assert m is not None, 'Postgres is not configured (DB_HOST/DB_USER/...)'; m.initialize_schema()"

db-reset: ## Reset database (WARNING: deletes all data)
	@echo "$(RED)Resetting database...$(NC)"
	$(COMPOSE) down postgres
	docker volume rm scraping_project_postgres_data || true
	@make docker-up
	@sleep 5
	@make db-migrate

##@ Monitoring

logs: docker-logs ## View logs (alias)

metrics: ## Open Grafana dashboard
	@echo "$(BLUE)Opening Grafana...$(NC)"
	$(OPEN) http://localhost:3000

prometheus: ## Open Prometheus UI
	@echo "$(BLUE)Opening Prometheus...$(NC)"
	$(OPEN) http://localhost:9090

##@ Documentation

docs: ## Show testing documentation (tests/README.md)
	@echo "$(BLUE)Testing documentation: tests/README.md$(NC)"
	@cat tests/README.md

docs-coverage: test-coverage ## Generate and open coverage report
	@echo "$(BLUE)Opening coverage report...$(NC)"
	$(OPEN) htmlcov/index.html

##@ Benchmarks

benchmark: ## Run performance benchmarks
	@echo "$(BLUE)Running performance benchmarks...$(NC)"
	pytest tests/unit -m performance --durations=0 -v

##@ Quick Commands

quick-test: ## Quick test run (unit tests only, no coverage)
	pytest tests/unit -v --tb=short -x

quick-check: ## Quick lint and test
	@make lint-fix
	@make quick-test

quick-commit: quick-check ## Quick check before commit
	@echo "$(GREEN)✓ Ready to commit!$(NC)"

##@ Utility

show-config: ## Show current configuration
	@echo "$(BLUE)Current Configuration:$(NC)"
	@echo "Python version: $$(python --version)"
	@echo "Pip version: $$(pip --version)"
	@echo "Docker version: $$(docker --version)"
	@echo "Docker Compose version: $$($(COMPOSE) version)"
	@echo ""
	@echo "Installed packages:"
	@pip list | head -20

tree: ## Show project structure
	tree -L 3 -I '__pycache__|*.pyc|.git|.venv|htmlcov|.pytest_cache|.ruff_cache'

size: ## Show project size
	@echo "$(BLUE)Project Size:$(NC)"
	@du -sh .
	@echo ""
	@echo "$(BLUE)Largest directories:$(NC)"
	@du -sh */ | sort -hr | head -10
