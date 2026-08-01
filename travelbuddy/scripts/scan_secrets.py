#!/usr/bin/env python3
"""Fail on high-confidence credentials committed to the repository.

This scans Git-tracked and non-ignored untracked text files. Local ``.env``
files are ignored by Git and never opened, so running the quality suite cannot
leak a developer's credentials into a terminal or CI log.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from collections import Counter
from pathlib import Path


SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("stripe-live-key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)

GENERIC_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password)"
    r"\s*[:=]\s*['\"]([^'\"\s]{20,})['\"]"
)
PLACEHOLDER_MARKERS = {
    "changeme",
    "dummy",
    "example",
    "fake",
    "not-a-secret",
    "placeholder",
    "test-key",
    "your-",
}
SKIP_SUFFIXES = {
    ".avif", ".db", ".doc", ".docx", ".gif", ".ico", ".jpeg", ".jpg",
    ".lock", ".pdf", ".png", ".sqlite", ".svg", ".ttf", ".webp", ".woff", ".woff2",
}


def entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def candidate_files(root: Path) -> list[Path]:
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    )
    return [root / item.decode() for item in completed.stdout.split(b"\0") if item]


def scan(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in candidate_files(root):
        relative = path.relative_to(root)
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            findings.append({"rule": "tracked-env-file", "file": str(relative), "line": 1})
            continue
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            for rule, pattern in SIGNATURES:
                if pattern.search(line):
                    findings.append({"rule": rule, "file": str(relative), "line": line_number})
            for match in GENERIC_ASSIGNMENT.finditer(line):
                value = match.group(1)
                lowered = value.lower()
                if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
                    continue
                if entropy(value) >= 3.5:
                    findings.append({
                        "rule": "high-entropy-credential",
                        "file": str(relative),
                        "line": line_number,
                    })
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="Git repository root")
    parser.add_argument("--output", type=Path, help="Write a redacted JSON report")
    args = parser.parse_args()

    root = args.root or Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    findings = scan(root.resolve())
    report = {"status": "failed" if findings else "passed", "findings": findings}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if findings:
        print(f"Secret scan found {len(findings)} potential credential(s):")
        for finding in findings:
            print(f"  {finding['file']}:{finding['line']} [{finding['rule']}]")
        print("Values are deliberately redacted. Remove the credential and rotate it before retrying.")
        return 1
    print("Secret scan passed: no high-confidence credentials in Git-visible source files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
