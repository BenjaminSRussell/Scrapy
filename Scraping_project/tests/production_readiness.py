"""Test configuration and benchmarks for production readiness assessment"""

import time
import psutil
import os
from pathlib import Path
from typing import Dict, Any, List

# Performance benchmarks for production readiness
PERFORMANCE_BENCHMARKS = {
    "url_canonicalization": {
        "operations_per_second": 10000,  # URLs/sec
        "memory_per_operation": 0.001,   # MB per URL
        "max_duration": 0.1              # seconds for 1000 URLs
    },
    "pipeline_throughput": {
        "stage1_discovery": 100,         # URLs/sec discovery
        "stage2_validation": 50,         # URLs/sec validation
        "stage3_enrichment": 10,         # URLs/sec enrichment
        "memory_efficiency": 100         # MB max for 1000 URLs
    },
    "integration_limits": {
        "max_memory_mb": 500,            # Max memory for full pipeline
        "max_duration_sec": 300,         # Max time for 1000 URL test
        "min_success_rate": 0.95         # 95% operations must succeed
    }
}

# Test data specifications
TEST_DATA_SPECS = {
    "unit_tests": {
        "url_count": 100,
        "timeout": 30,
        "memory_limit_mb": 50
    },
    "integration_tests": {
        "url_count": 1000,
        "timeout": 300,
        "memory_limit_mb": 200
    },
    "load_tests": {
        "url_count": 5000,
        "timeout": 600,
        "memory_limit_mb": 500
    },
    "endurance_tests": {
        "url_count": 10000,
        "timeout": 1800,
        "memory_limit_mb": 1000
    }
}

class ProductionReadinessChecker:
    """Check if system meets production readiness criteria"""

    def __init__(self):
        self.criteria = {
            "critical_tests_pass": False,
            "performance_benchmarks_met": False,
            "memory_efficiency_acceptable": False,
            "error_rate_acceptable": False,
            "scalability_proven": False
        }

    def check_performance_benchmark(self, test_name: str, metrics: Dict[str, Any]) -> bool:
        """Check if performance metrics meet benchmark requirements"""
        if test_name not in PERFORMANCE_BENCHMARKS:
            return True  # No specific benchmark defined

        benchmark = PERFORMANCE_BENCHMARKS[test_name]

        # Check operations per second
        if "operations_per_second" in benchmark:
            actual_ops = metrics.get("ops_per_second", 0)
            required_ops = benchmark["operations_per_second"]
            if actual_ops < required_ops * 0.8:  # Allow 20% tolerance
                return False

        # Check memory efficiency
        if "memory_per_operation" in benchmark:
            actual_memory = metrics.get("memory_delta_mb", 0) / max(1, metrics.get("operations", 1))
            required_memory = benchmark["memory_per_operation"]
            if actual_memory > required_memory * 2:  # Allow 100% tolerance for memory
                return False

        # Check duration limits
        if "max_duration" in benchmark:
            actual_duration = metrics.get("duration", 0)
            max_duration = benchmark["max_duration"]
            if actual_duration > max_duration * 2:  # Allow 100% tolerance
                return False

        return True

    def evaluate_readiness(self, test_results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Evaluate overall production readiness"""
        readiness_score = 0
        total_criteria = len(self.criteria)

        # Check critical tests
        critical_passed = all(
            result.get("success", False)
            for test_name, result in test_results.items()
            if "critical" in test_name.lower()
        )
        self.criteria["critical_tests_pass"] = critical_passed
        if critical_passed:
            readiness_score += 1

        # Check performance benchmarks
        performance_met = True
        for test_name, result in test_results.items():
            metrics = result.get("metrics", {})
            if not self.check_performance_benchmark(test_name, metrics):
                performance_met = False
                break
        self.criteria["performance_benchmarks_met"] = performance_met
        if performance_met:
            readiness_score += 1

        # Check memory efficiency
        max_memory_usage = max(
            result.get("metrics", {}).get("memory_delta_mb", 0)
            for result in test_results.values()
        )
        memory_acceptable = max_memory_usage < PERFORMANCE_BENCHMARKS["integration_limits"]["max_memory_mb"]
        self.criteria["memory_efficiency_acceptable"] = memory_acceptable
        if memory_acceptable:
            readiness_score += 1

        # Check error rates
        total_tests = len(test_results)
        passed_tests = sum(1 for result in test_results.values() if result.get("success", False))
        success_rate = passed_tests / max(1, total_tests)
        error_rate_acceptable = success_rate >= PERFORMANCE_BENCHMARKS["integration_limits"]["min_success_rate"]
        self.criteria["error_rate_acceptable"] = error_rate_acceptable
        if error_rate_acceptable:
            readiness_score += 1

        # Check scalability (based on load test performance)
        scalability_proven = any(
            "load" in test_name.lower() and result.get("success", False)
            for test_name, result in test_results.items()
        )
        self.criteria["scalability_proven"] = scalability_proven
        if scalability_proven:
            readiness_score += 1

        readiness_percentage = (readiness_score / total_criteria) * 100

        return {
            "ready_for_production": readiness_percentage >= 80,
            "readiness_percentage": readiness_percentage,
            "criteria_met": self.criteria,
            "recommendations": self._generate_recommendations()
        }

    def _generate_recommendations(self) -> List[str]:
        """Generate recommendations based on failed criteria"""
        recommendations = []

        if not self.criteria["critical_tests_pass"]:
            recommendations.append("❌ Fix critical test failures before production deployment")

        if not self.criteria["performance_benchmarks_met"]:
            recommendations.append("⚡ Optimize performance to meet benchmark requirements")

        if not self.criteria["memory_efficiency_acceptable"]:
            recommendations.append("🧠 Reduce memory usage for better efficiency")

        if not self.criteria["error_rate_acceptable"]:
            recommendations.append("🐛 Improve error handling and reduce failure rates")

        if not self.criteria["scalability_proven"]:
            recommendations.append("📈 Conduct load testing to prove scalability")

        if not recommendations:
            recommendations.append("✅ System meets all production readiness criteria")

        return recommendations

def get_system_resources() -> Dict[str, Any]:
    """Get current system resource information"""
    process = psutil.Process(os.getpid())
    return {
        "cpu_count": psutil.cpu_count(),
        "memory_total_gb": psutil.virtual_memory().total / (1024**3),
        "memory_available_gb": psutil.virtual_memory().available / (1024**3),
        "disk_free_gb": psutil.disk_usage(Path.cwd()).free / (1024**3),
        "process_memory_mb": process.memory_info().rss / (1024**2),
        "process_cpu_percent": process.cpu_percent()
    }

def validate_test_environment() -> Dict[str, Any]:
    """Validate that test environment meets minimum requirements"""
    resources = get_system_resources()

    requirements = {
        "min_memory_gb": 2,
        "min_disk_gb": 1,
        "min_cpu_cores": 2
    }

    validation = {
        "sufficient_memory": resources["memory_available_gb"] >= requirements["min_memory_gb"],
        "sufficient_disk": resources["disk_free_gb"] >= requirements["min_disk_gb"],
        "sufficient_cpu": resources["cpu_count"] >= requirements["min_cpu_cores"],
        "requirements": requirements,
        "current_resources": resources
    }

    validation["environment_ready"] = all([
        validation["sufficient_memory"],
        validation["sufficient_disk"],
        validation["sufficient_cpu"]
    ])

    return validation