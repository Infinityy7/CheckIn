"""Regression tests for the redacted, dependency-free secret scanner."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "scan_secrets.py"
SPEC = importlib.util.spec_from_file_location("travelbuddy_secret_scan", MODULE_PATH)
assert SPEC and SPEC.loader
secret_scan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(secret_scan)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_secret_scan_ignores_examples_but_flags_high_confidence_untracked_source(tmp_path):
    git(tmp_path, "init", "--quiet")
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=local-value-must-never-be-read\n", encoding="utf-8")
    (tmp_path / "safe.py").write_text('OPENAI_API_KEY = "test-key-not-a-secret"\n', encoding="utf-8")
    git(tmp_path, "add", ".gitignore", ".env.example", "safe.py")

    assert secret_scan.scan(tmp_path) == []

    # Construct the value so this test file itself never resembles a credential.
    candidate = "sk-" + ("A" * 32)
    (tmp_path / "new_provider.py").write_text(f'API_KEY = "{candidate}"\n', encoding="utf-8")
    findings = secret_scan.scan(tmp_path)

    assert findings == [{"rule": "openai-key", "file": "new_provider.py", "line": 1}]
    assert candidate not in repr(findings)


def test_secret_scan_rejects_a_tracked_environment_file(tmp_path):
    git(tmp_path, "init", "--quiet")
    (tmp_path / ".env.production").write_text("EMPTY=\n", encoding="utf-8")
    git(tmp_path, "add", "-f", ".env.production")

    assert secret_scan.scan(tmp_path) == [
        {"rule": "tracked-env-file", "file": ".env.production", "line": 1}
    ]
