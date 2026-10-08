"""Local mirror of the CI matrix (#350).

    pip install nox            # or: pipx run nox ...
    nox                        # lint + tests on every Python in PYTHONS that is installed
    nox -s lint                # ruff + mypy + bandit, exactly CI's gates
    nox -s tests-3.12          # one interpreter
    nox -s tests -- tests/unit -x   # extra args go to pytest

PYTHONS must match ``python-version`` in .github/workflows/main.yml.
tests/unit/test_contributor_tooling.py fails if they drift apart.
"""

import nox

PYTHONS = ["3.11", "3.12"]
PYTEST_MARKERS = "not slow and not kafka and not performance"
REQUIREMENTS = ["-r", "requirements.txt", "-r", "dev-requirements.txt", "-r", "ci-tools.txt"]

nox.options.sessions = ["lint", "tests"]
nox.options.reuse_existing_virtualenvs = True


@nox.session(python=PYTHONS[0])
def lint(session: nox.Session) -> None:
    session.install(*REQUIREMENTS)
    session.run("ruff", "check", "src/", "--select", "F,E4,E7,E9")
    session.run("mypy", "src/", "--config-file", "mypy.ini", "--ignore-missing-imports", "--no-strict-optional")
    session.run("bandit", "-r", "src/", "-ll", "-q")


@nox.session(python=PYTHONS)
def tests(session: nox.Session) -> None:
    session.install(*REQUIREMENTS)
    args = session.posargs or ["tests/"]
    session.run(
        "python", "-m", "pytest", *args,
        "-m", PYTEST_MARKERS, "-o", "addopts=", "--strict-markers", "-q",
        env={"OBS_OFFLINE": "1"},
    )
