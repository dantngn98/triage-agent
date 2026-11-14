import importlib

MODULES = [
    "triage", "triage.config", "triage.schemas", "triage.llm", "triage.tools",
    "triage.pipeline", "triage.validator", "triage.approval", "triage.tracing",
    "triage.board", "triage.__main__",
]


def test_package_modules_import():
    for name in MODULES:
        importlib.import_module(name)
