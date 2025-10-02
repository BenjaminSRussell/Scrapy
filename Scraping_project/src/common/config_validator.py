"""
Configuration validation utilities and health checks.

Provides comprehensive validation beyond Pydantic schema validation,
including runtime dependency checks, file system validation, and
configuration health reporting.
"""

import logging
from pathlib import Path
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """Represents a configuration validation issue"""
    severity: str  # 'error', 'warning', 'info'
    category: str  # 'schema', 'filesystem', 'dependency', 'logic'
    message: str
    suggestion: str = ""


class ConfigHealthCheck:
    """
    Comprehensive configuration health checker.

    Performs validation beyond Pydantic schema validation:
    - File system checks (paths, permissions)
    - Dependency availability (libraries, models)
    - Logical consistency (cross-field validation)
    - Performance implications (resource settings)
    """

    def __init__(self, config):
        """
        Initialize health checker with configuration.

        Args:
            config: Config instance or PipelineConfig instance
        """
        self.config = config
        self.issues: List[ValidationIssue] = []

    def run_all_checks(self) -> Tuple[bool, List[ValidationIssue]]:
        """
        Run all health checks.

        Returns:
            Tuple of (is_healthy, list_of_issues)
            is_healthy is False if any error-level issues found
        """
        self.issues = []

        # Run all validation checks
        self._check_file_system()
        self._check_dependencies()
        self._check_resource_limits()
        self._check_performance_settings()

        # Determine overall health
        has_errors = any(issue.severity == 'error' for issue in self.issues)

        return (not has_errors, self.issues)

    def _check_file_system(self):
        """Validate file system paths and permissions"""
        # Check if this is a Config object or PipelineConfig
        if hasattr(self.config, '_validated_config'):
            # Config object
            validated = self.config._validated_config
            if not validated:
                return
        else:
            # PipelineConfig object
            validated = self.config

        # Check seed file
        seed_file = validated.stages.discovery.seed_file
        if seed_file:
            seed_path = Path(seed_file)
            if not seed_path.exists():
                self.issues.append(ValidationIssue(
                    severity='error',
                    category='filesystem',
                    message=f"Seed file not found: {seed_file}",
                    suggestion=f"Create the seed file or update the path in configuration"
                ))
            elif not seed_path.is_file():
                self.issues.append(ValidationIssue(
                    severity='error',
                    category='filesystem',
                    message=f"Seed path is not a file: {seed_file}",
                    suggestion="Ensure seed_file points to a valid CSV file"
                ))

        # Check dedup cache directory
        dedup_path = Path(validated.stages.discovery.dedup_cache_path)
        dedup_dir = dedup_path.parent
        if not dedup_dir.exists():
            try:
                dedup_dir.mkdir(parents=True, exist_ok=True)
                self.issues.append(ValidationIssue(
                    severity='info',
                    category='filesystem',
                    message=f"Created dedup cache directory: {dedup_dir}",
                    suggestion=""
                ))
            except Exception as e:
                self.issues.append(ValidationIssue(
                    severity='error',
                    category='filesystem',
                    message=f"Cannot create dedup cache directory: {dedup_dir}. Error: {e}",
                    suggestion="Check directory permissions or update dedup_cache_path"
                ))

        # Check data directories are writable
        data_dirs = {
            'raw_dir': validated.data.raw_dir,
            'processed_dir': validated.data.processed_dir,
            'cache_dir': validated.data.cache_dir,
            'logs_dir': validated.data.logs_dir,
        }

        for dir_name, dir_path in data_dirs.items():
            path = Path(dir_path)
            if not path.exists():
                try:
                    path.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    self.issues.append(ValidationIssue(
                        severity='error',
                        category='filesystem',
                        message=f"Cannot create {dir_name}: {dir_path}. Error: {e}",
                        suggestion=f"Check permissions or update {dir_name} in configuration"
                    ))

    def _check_dependencies(self):
        """Check for required dependencies"""
        if hasattr(self.config, '_validated_config'):
            validated = self.config._validated_config
            if not validated:
                return
        else:
            validated = self.config

        # Check NLP dependencies
        if validated.stages.enrichment.nlp_enabled:
            try:
                import spacy
                model_name = validated.nlp.spacy_model or validated.nlp.model
                if model_name:
                    try:
                        spacy.load(model_name)
                    except OSError:
                        self.issues.append(ValidationIssue(
                            severity='error',
                            category='dependency',
                            message=f"spaCy model '{model_name}' not installed",
                            suggestion=f"Run: python -m spacy download {model_name}"
                        ))
            except ImportError:
                self.issues.append(ValidationIssue(
                    severity='error',
                    category='dependency',
                    message="spaCy not installed but NLP is enabled",
                    suggestion="Run: pip install spacy"
                ))

            # Check transformer dependencies if enabled
            if validated.nlp.use_transformers:
                try:
                    import transformers
                except ImportError:
                    self.issues.append(ValidationIssue(
                        severity='error',
                        category='dependency',
                        message="transformers library not installed but use_transformers is enabled",
                        suggestion="Run: pip install transformers torch"
                    ))

        # Check headless browser dependencies
        stages_to_check = [
            ('discovery', validated.stages.discovery.headless_browser),
            ('enrichment', validated.stages.enrichment.headless_browser)
        ]

        for stage_name, browser_config in stages_to_check:
            if browser_config.enabled:
                if browser_config.engine == 'playwright':
                    try:
                        import playwright
                        # Check if browsers are installed
                        try:
                            from playwright.sync_api import sync_playwright
                            with sync_playwright() as p:
                                # Try to get browser - this will fail if not installed
                                browser_type = getattr(p, browser_config.browser_type)
                        except Exception:
                            self.issues.append(ValidationIssue(
                                severity='warning',
                                category='dependency',
                                message=f"Playwright browsers not installed for {stage_name} stage",
                                suggestion="Run: playwright install"
                            ))
                    except ImportError:
                        self.issues.append(ValidationIssue(
                            severity='error',
                            category='dependency',
                            message=f"Playwright not installed but enabled in {stage_name} stage",
                            suggestion="Run: pip install playwright && playwright install"
                        ))
                elif browser_config.engine == 'selenium':
                    try:
                        import selenium
                    except ImportError:
                        self.issues.append(ValidationIssue(
                            severity='error',
                            category='dependency',
                            message=f"Selenium not installed but enabled in {stage_name} stage",
                            suggestion="Run: pip install selenium"
                        ))

    def _check_resource_limits(self):
        """Check for potential resource limit issues"""
        if hasattr(self.config, '_validated_config'):
            validated = self.config._validated_config
            if not validated:
                return
        else:
            validated = self.config

        # Check for excessive concurrency
        concurrent_requests = validated.scrapy.concurrent_requests
        if concurrent_requests > 100:
            self.issues.append(ValidationIssue(
                severity='warning',
                category='logic',
                message=f"Very high concurrent_requests: {concurrent_requests}",
                suggestion="Consider lowering to avoid overwhelming target servers and local resources"
            ))

        # Check validation workers
        max_workers = validated.stages.validation.max_workers
        if max_workers > 50:
            self.issues.append(ValidationIssue(
                severity='warning',
                category='logic',
                message=f"Very high validation max_workers: {max_workers}",
                suggestion="Consider lowering to avoid resource exhaustion"
            ))

        # Check queue size
        queue_size = validated.queue.max_queue_size
        if queue_size > 100000:
            self.issues.append(ValidationIssue(
                severity='warning',
                category='logic',
                message=f"Very large queue size: {queue_size}",
                suggestion="Large queues may consume significant memory"
            ))

        # Check headless browser concurrent limits
        for stage_name, browser_config in [
            ('discovery', validated.stages.discovery.headless_browser),
            ('enrichment', validated.stages.enrichment.headless_browser)
        ]:
            if browser_config.enabled:
                limit = browser_config.concurrent_limit
                if limit > 5:
                    self.issues.append(ValidationIssue(
                        severity='warning',
                        category='logic',
                        message=f"High browser concurrent_limit in {stage_name}: {limit}",
                        suggestion="Browsers are resource-intensive. Consider limit <= 5"
                    ))

    def _check_performance_settings(self):
        """Check for performance-related configuration issues"""
        if hasattr(self.config, '_validated_config'):
            validated = self.config._validated_config
            if not validated:
                return
        else:
            validated = self.config

        # Check download delay
        if validated.scrapy.download_delay == 0:
            self.issues.append(ValidationIssue(
                severity='warning',
                category='logic',
                message="download_delay is 0 - no rate limiting",
                suggestion="Consider adding a small delay to be respectful to target servers"
            ))

        # Check if retry is disabled
        if not validated.scrapy.retry_enabled:
            self.issues.append(ValidationIssue(
                severity='info',
                category='logic',
                message="Retry is disabled",
                suggestion="May miss URLs due to transient failures"
            ))

        # Check NLP text length limits
        if validated.stages.enrichment.nlp_enabled:
            max_length = validated.stages.enrichment.max_text_length
            if max_length > 100000:
                self.issues.append(ValidationIssue(
                    severity='warning',
                    category='logic',
                    message=f"Very large max_text_length: {max_length}",
                    suggestion="Processing large texts may be slow and memory-intensive"
                ))

    def print_report(self):
        """Print a formatted health check report"""
        if not self.issues:
            print("\n[✓] Configuration Health Check: PASSED")
            print("No issues found.\n")
            return

        # Group issues by severity
        errors = [i for i in self.issues if i.severity == 'error']
        warnings = [i for i in self.issues if i.severity == 'warning']
        info = [i for i in self.issues if i.severity == 'info']

        print("\n" + "=" * 80)
        print("Configuration Health Check Report")
        print("=" * 80)

        if errors:
            print(f"\n[X] ERRORS ({len(errors)}):")
            print("-" * 80)
            for issue in errors:
                print(f"\n  [{issue.category.upper()}] {issue.message}")
                if issue.suggestion:
                    print(f"  [!] {issue.suggestion}")

        if warnings:
            print(f"\n[!] WARNINGS ({len(warnings)}):")
            print("-" * 80)
            for issue in warnings:
                print(f"\n  [{issue.category.upper()}] {issue.message}")
                if issue.suggestion:
                    print(f"  [!] {issue.suggestion}")

        if info:
            print(f"\n[i] INFO ({len(info)}):")
            print("-" * 80)
            for issue in info:
                print(f"\n  [{issue.category.upper()}] {issue.message}")
                if issue.suggestion:
                    print(f"  [!] {issue.suggestion}")

        print("\n" + "=" * 80)

        if errors:
            print("[X] Status: FAILED - Please fix errors before running pipeline")
        elif warnings:
            print("[!] Status: PASSED WITH WARNINGS - Review warnings before running")
        else:
            print("[✓] Status: PASSED")

        print("=" * 80 + "\n")


def validate_config_health(config) -> bool:
    """
    Convenience function to run health check and print report.

    Args:
        config: Config or PipelineConfig instance

    Returns:
        bool: True if no errors, False otherwise
    """
    checker = ConfigHealthCheck(config)
    is_healthy, issues = checker.run_all_checks()
    checker.print_report()
    return is_healthy
