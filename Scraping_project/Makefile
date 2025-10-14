.PHONY: help install install-dev test test-unit test-integration test-coverage lint format security clean docker-up docker-down pre-commit ci

# Default target
.DEFAULT_GOAL := help

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m  # No Color

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
	pre-commit install
	@echo "$(GREEN)✓ Development environment ready!$(NC)"

update-deps: ## Update and compile dependencies
	@echo "$(BLUE)Updating dependencies...$(NC)"
	pip install --upgrade pip pip-tools
	pip-compile requirements.in --upgrade
	pip-compile dev-requirements.in --upgrade
	@echo "$(GREEN)✓ Dependencies updated$(NC)"

##@ Testing

test: ## Run all tests
	@echo "$(BLUE)Running all tests...$(NC)"
	pytest -v

test-unit: ## Run unit tests only
	@echo "$(BLUE)Running unit tests...$(NC)"
	pytest tests/unit -v -m unit

test-integration: ## Run integration tests (requires services)
	@echo "$(BLUE)Running integration tests...$(NC)"
	pytest tests/integration -v -m integration

test-fast: ## Run fast tests only (excluding slow)
	@echo "$(BLUE)Running fast tests...$(NC)"
	pytest -v -m "not slow"

test-coverage: ## Run tests with coverage report
	@echo "$(BLUE)Running tests with coverage...$(NC)"
	pytest --cov=src --cov-report=html --cov-report=term-missing
	@echo "$(GREEN)✓ Coverage report generated at htmlcov/index.html$(NC)"

test-coverage-min: ## Check minimum coverage threshold
	@echo "$(BLUE)Checking coverage threshold...$(NC)"
	pytest --cov=src --cov-fail-under=70

test-watch: ## Run tests in watch mode
	@echo "$(BLUE)Running tests in watch mode...$(NC)"
	pytest-watch

##@ Code Quality

lint: ## Run all linters
	@echo "$(BLUE)Running linters...$(NC)"
	ruff check .
	black --check src tests
	isort --check-only src tests
	flake8 src tests
	@echo "$(GREEN)✓ Linting complete$(NC)"

lint-fix: ## Fix linting issues automatically
	@echo "$(BLUE)Fixing linting issues...$(NC)"
	ruff check --fix .
	black src tests
	isort src tests
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
	docker-compose build --parallel

docker-up: ## Start Docker services
	@echo "$(BLUE)Starting Docker services...$(NC)"
	docker-compose up -d
	@echo "$(GREEN)✓ Services started$(NC)"
	@echo "Run 'make docker-logs' to view logs"

docker-down: ## Stop Docker services
	@echo "$(BLUE)Stopping Docker services...$(NC)"
	docker-compose down
	@echo "$(GREEN)✓ Services stopped$(NC)"

docker-down-clean: ## Stop and remove volumes
	@echo "$(YELLOW)Stopping services and removing volumes...$(NC)"
	docker-compose down -v
	@echo "$(GREEN)✓ Services and volumes removed$(NC)"

docker-logs: ## View Docker logs
	docker-compose logs -f

docker-ps: ## Show running containers
	docker-compose ps

docker-shell: ## Open shell in scrapy container
	docker-compose exec scrapy-app /bin/bash

docker-test: docker-up ## Run tests in Docker
	@echo "$(BLUE)Running tests in Docker...$(NC)"
	@sleep 5  # Wait for services to be ready
	docker-compose exec -T scrapy-app pytest -v
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
	@echo "Prometheus: http://localhost:9091"

stop: docker-down ## Stop all services

restart: docker-down docker-up ## Restart all services

shell: ## Open Python shell with project context
	python -i -c "import sys; sys.path.insert(0, 'src')"

notebook: ## Start Jupyter notebook
	jupyter notebook

##@ Database

db-migrate: ## Run database migrations
	@echo "$(BLUE)Running database migrations...$(NC)"
	python -m alembic upgrade head

db-reset: ## Reset database (WARNING: deletes all data)
	@echo "$(RED)Resetting database...$(NC)"
	docker-compose down postgres
	docker volume rm scraping_project_postgres_data || true
	@make docker-up
	@sleep 5
	@make db-migrate

##@ Monitoring

logs: docker-logs ## View logs (alias)

metrics: ## Open Grafana dashboard
	@echo "$(BLUE)Opening Grafana...$(NC)"
	open http://localhost:3000

prometheus: ## Open Prometheus UI
	@echo "$(BLUE)Opening Prometheus...$(NC)"
	open http://localhost:9091

##@ Documentation

docs: ## Open testing documentation
	@echo "$(BLUE)Opening documentation...$(NC)"
	open TESTING.md || cat TESTING.md

docs-coverage: test-coverage ## Generate and open coverage report
	@echo "$(BLUE)Opening coverage report...$(NC)"
	open htmlcov/index.html

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
	@echo "Docker Compose version: $$(docker-compose --version)"
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
