from src.log_redaction import install_log_redaction as _install_log_redaction

# Redact credentials from every log record in every process that imports src (#680).
_install_log_redaction()

__version__ = "0.2.0"
__author__ = "Benjamin Russell"
__all__ = [
    "common",
    "orchestrator",
    "stage1",
    "stage2",
    "stage3",
]
