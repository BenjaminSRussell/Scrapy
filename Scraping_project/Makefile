.PHONY: help install install-dev clean format lint type-check test test-cov test-fast \
        run-stage1 run-stage2 run-stage3 run-all clean-data clean-cache clean-logs \
        reset-checkpoints docs build

# Default target
help:
	@echo "UConn Web Scraping Pipeline - Makefile Commands"
	@echo ""
	@echo "Development Setup:"
	@echo "  make install          Install core dependencies"
	@echo "  make install-dev      Install with development dependencies"
	@echo "  make install-nlp      Install with NLP extras (transformers)"
	@echo "  make install-all      Install everything"
	@echo ""
	@echo "Code Quality:"
	@echo "  make format           Format code with black and isort"
	@echo "  make lint             Run flake8 linter"
	@echo "  make type-check       Run mypy type checking"
	@echo "  make quality          Run all quality checks"
	@echo ""
	@echo "Testing:"
	@echo "  make test             Run pytest with standard options"
	@echo "  make test-cov         Run tests with coverage report"
	@echo "  make test-fast        Run tests without slow tests"
	@echo "  make test-integration Run integration tests only"
	@echo ""
	@echo "Pipeline Operations:"
	@echo "  make run-stage1       Run Stage 1 (Discovery)"
	@echo "  make run-stage2       Run Stage 2 (Validation)"
	@echo "  make run-stage3       Run Stage 3 (Enrichment)"
	@echo "  make run-all          Run full pipeline (all stages)"
	@echo ""
	@echo "Data Management:"
	@echo "  make clean-data       Clear processed data directories"
	@echo "  make clean-cache      Clear cache directories"
	@echo "  make clean-logs       Clear log files"
	@echo "  make reset-checkpoints Reset pipeline checkpoints"
	@echo "  make clean            Clean all generated files"
	@echo ""
	@echo "Build & Distribution:"
	@echo "  make build            Build distribution packages"
	@echo "  make clean-build      Remove build artifacts"

# Installation targets
install:
	pip install -e .
	python -m spacy download en_core_web_sm

install-dev:
	pip install -e ".[dev]"
	python -m spacy download en_core_web_sm
	pre-commit install

install-nlp:
	pip install -e ".[nlp]"

install-all:
	pip install -e ".[all]"
	python -m spacy download en_core_web_sm
	pre-commit install

# Code quality targets
format:
	@echo "Formatting code with black..."
	black src tests
	@echo "Sorting imports with isort..."
	isort src tests

lint:
	@echo "Running flake8..."
	flake8 src tests --max-line-length=100 --extend-ignore=E203,W503

type-check:
	@echo "Running mypy type checking..."
	mypy src

quality: format lint type-check
	@echo "All quality checks passed!"

# Testing targets
test:
	pytest

test-cov:
	pytest --cov=src --cov-report=html --cov-report=term

test-fast:
	pytest -m "not slow" -x

test-integration:
	pytest -m integration

# Pipeline operation targets
run-stage1:
	python -m src.orchestrator.main --stage 1

run-stage2:
	python -m src.orchestrator.main --stage 2

run-stage3:
	python -m src.orchestrator.main --stage 3

run-all:
	python -m src.orchestrator.main --stage all

# Data management targets
clean-data:
	@echo "Cleaning processed data..."
	find data/processed -type f ! -name '.gitkeep' -delete
	@echo "Data cleaned"

clean-cache:
	@echo "Cleaning cache directories..."
	find data/cache -type f ! -name '.gitkeep' -delete
	rm -rf .scrapy
	@echo "Cache cleaned"

clean-logs:
	@echo "Cleaning log files..."
	find data/logs -type f ! -name '.gitkeep' -delete
	find logs -type f -delete 2>/dev/null || true
	@echo "Logs cleaned"

reset-checkpoints:
	@echo "Resetting checkpoints..."
	find data/checkpoints -type f ! -name '.gitkeep' -delete
	@echo "Checkpoints reset"

# Build targets
build:
	python -m build

clean-build:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete
	find . -type f -name '*~' -delete

# Comprehensive clean
clean: clean-build clean-cache clean-logs
	@echo "Cleaning test artifacts..."
	rm -rf .pytest_cache
	rm -rf .coverage
	rm -rf htmlcov
	rm -rf tests/output/*
	rm -rf tests/reports/*
	@echo "Clean complete!"

# Documentation
docs:
	@echo "Documentation is in the docs/ directory"
	@echo "View README.md for getting started"
