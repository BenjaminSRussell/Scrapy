# Every recipe is phony (none produce a file named after the target) (#796).
.PHONY: help cli-help install install-dev install-stage4 lock update-deps test test-unit test-integration test-fast \
	test-ci test-perf test-coverage test-coverage-min test-watch lint lint-fix format typecheck security \
	security-full docker-build docker-up docker-down docker-down-clean docker-logs docker-ps \
	docker-shell docker-test pre-commit pre-commit-update ci ci-docker clean clean-all dev \
	serve stop restart shell notebook db-migrate db-reset logs metrics prometheus docs \
	docs-coverage benchmark quick-test quick-check quick-commit show-config tree size \
	profile models backup restore anomaly-scan

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

# Groups that already exist: core (lint/test/docker path), optional (heavy extras),
# k8s (helm/kubectl targets). Help lists a name only when the target is real (#840).
# anomaly-scan is optional: offline fixture only, not the core lint/test path (#501).
help: ## Display this help message
	@echo "$(BLUE)Scraping Pipeline - Development Commands$(NC)"
	@echo ""
	@awk 'BEGIN { FS = ":.*##"; split("core optional k8s", order, " ") } \
		/^[a-zA-Z_0-9-]+:.*?##/ { \
			name = $$1; desc = $$2; gsub(/^[ \t]+/, "", desc); \
			g = "core"; \
			if (index(name, "k8s") || index(name, "helm") || index(name, "kubectl")) g = "k8s"; \
			else if (name == "install-stage4" || name == "models" || name == "test-perf" || name == "benchmark" || name == "security-full" || name == "anomaly-scan" || index(name, "playwright") || index(name, "kafka") || index(name, "stage4")) g = "optional"; \
			n[g]++; keys[g, n[g]] = name; text[g, n[g]] = desc; \
		} \
		END { \
			printf "Usage:\n  make $(YELLOW)<target>$(NC)\n"; \
			for (i = 1; i <= 3; i++) { \
				g = order[i]; \
				printf "\n$(BLUE)%s$(NC)\n", g; \
				if (!(g in n) || n[g] == 0) { \
					if (g == "k8s") printf "  (no Make target in this file)\n"; \
					else printf "  (none)\n"; \
					continue; \
				} \
				for (j = 1; j <= n[g]; j++) printf "  $(GREEN)%-20s$(NC) %s\n", keys[g, j], text[g, j]; \
			} \
		}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Ops CLI: python cli.py --help   (health, validate, seeds, export, queue-gc, killswitch, reset, ...; see make cli-help)"

cli-help: ## List the ops CLI (cli.py) subcommands
	python cli.py --help

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

# Optional and heavy (~2 GB of torch/transformers). Not a dependency of install
# or install-dev, and not an image build (#820). requirements-stage4.txt already
# exists and includes requirements-ml.txt; this target does not compile locks.
install-stage4: ## Optional, heavy Stage 4 NLP install (torch/transformers; not for CI)
	@echo "$(BLUE)Installing optional Stage 4 NLP dependencies (heavy; not required for CI)...$(NC)"
	pip install -r requirements-stage4.txt

# Lockfiles are compiled from this directory with relative paths so the generated
# header never embeds a machine-local path (#397). See ../CONTRIBUTING.md.
PIP_COMPILE = pip-compile --quiet --strip-extras

lock: ## Re-pin lockfiles after editing *.in (keeps existing versions)
	@echo "$(BLUE)Compiling lockfiles (no upgrades)...$(NC)"
	pip install --upgrade pip-tools
	cd "$(CURDIR)" && $(PIP_COMPILE) --output-file=requirements.txt requirements.in
	cd "$(CURDIR)" && $(PIP_COMPILE) --constraint=requirements.txt --output-file=requirements-ml.txt requirements-ml.in
	cd "$(CURDIR)" && $(PIP_COMPILE) --constraint=requirements.txt --constraint=requirements-ml.txt --output-file=requirements-ocr.txt requirements-ocr.in
	cd "$(CURDIR)" && $(PIP_COMPILE) --output-file=dev-requirements.txt dev-requirements.in
	@echo "$(GREEN)✓ Lockfiles compiled (core/dev stay torch-free; ML/OCR are separate)$(NC)"

update-deps: ## Upgrade every pin and recompile lockfiles
	@echo "$(BLUE)Updating dependencies...$(NC)"
	pip install --upgrade pip pip-tools
	cd "$(CURDIR)" && $(PIP_COMPILE) --upgrade --output-file=requirements.txt requirements.in
	cd "$(CURDIR)" && $(PIP_COMPILE) --upgrade --constraint=requirements.txt --output-file=requirements-ml.txt requirements-ml.in
	cd "$(CURDIR)" && $(PIP_COMPILE) --upgrade --constraint=requirements.txt --constraint=requirements-ml.txt --output-file=requirements-ocr.txt requirements-ocr.in
	cd "$(CURDIR)" && $(PIP_COMPILE) --upgrade --output-file=dev-requirements.txt dev-requirements.in
	@echo "$(GREEN)✓ Dependencies updated (core/dev stay torch-free)$(NC)"

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
	python cli.py --profile=data/exports/profiles $(CMD)

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

docker-up: ## Start core + monitoring Compose profiles (#145)
	@echo "$(BLUE)Starting Docker services...$(NC)"
	COMPOSE_PROFILES=$${COMPOSE_PROFILES:-core,monitoring} $(COMPOSE) up -d
	@echo "$(GREEN)✓ Services started$(NC)"
	@echo "Run 'make docker-logs' to view logs"

docker-down: ## Stop Docker services (every profile)
	@echo "$(BLUE)Stopping Docker services...$(NC)"
	COMPOSE_PROFILES=core,monitoring,streaming,observability,ml,full $(COMPOSE) down
	@echo "$(GREEN)✓ Services stopped$(NC)"

docker-down-clean: ## Stop and remove volumes
	@echo "$(YELLOW)Stopping services and removing volumes...$(NC)"
	COMPOSE_PROFILES=core,monitoring,streaming,observability,ml,full $(COMPOSE) down -v
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

##@ Backup

# Local dry-runs (#300). Core help group: they do not start services.
# DRY_RUN defaults to 1 inside the scripts when this is empty.
# DRY_RUN=0 requires LAKE_SRC and BACKUP_DIR under /tmp or this worktree.
backup: ## Dry-run local backup plan (no pg_dump or services). DRY_RUN=0 writes archives
	@env \
		DRY_RUN="$(DRY_RUN)" \
		BACKUP_DIR="$(BACKUP_DIR)" \
		RETENTION_DAYS="$(RETENTION_DAYS)" \
		RETENTION_COUNT="$(RETENTION_COUNT)" \
		LAKE_SRC="$(LAKE_SRC)" \
		POSTGRES_DUMP_FILE="$(POSTGRES_DUMP_FILE)" \
		BACKUP_ID="$(BACKUP_ID)" \
		./scripts/automated_backup.sh

restore: ## Dry-run restore plan (no database). DRY_RUN=0 extracts; ROUNDTRIP=1 checks a temp sample
	@env \
		DRY_RUN="$(DRY_RUN)" \
		BACKUP_DIR="$(BACKUP_DIR)" \
		BACKUP_ID="$(BACKUP_ID)" \
		RESTORE_DEST="$(RESTORE_DEST)" \
		RESTORE_SQL_DEST="$(RESTORE_SQL_DEST)" \
		ROUNDTRIP="$(ROUNDTRIP)" \
		./scripts/restore_backup.sh

##@ Anomaly

# Offline metrics fixture (#501). Stdlib z-score. Does not read data/delta_lake,
# train on the live lake, install packages, or POST to Alertmanager.
# FIXTURE defaults to the clean series (exit 0). A series past the threshold exits 1.
# Missing scikit-learn (see scripts/kmeans_anomaly_detector.py) does not fail this target.
FIXTURE ?= scripts/fixtures/anomaly/clean_metrics.json
anomaly-scan: ## Offline anomaly scan of a metrics fixture (no lake, no HTTP)
	@python3 scripts/anomaly_scan.py --fixture "$(FIXTURE)"

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
