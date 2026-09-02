"""Tests for ``nb_secrets``.

Importable as ``nb_secrets`` thanks to ``tests/conftest.py`` putting the tool
directory on ``sys.path``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import nb_secrets as nbs

SCRIPT = Path(__file__).resolve().parent.parent / "nb_secrets.py"

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
GH_TOKEN = "ghp_" + "a" * 36
DAPI = "dapi" + "0" * 32


# --- helpers ------------------------------------------------------- #


def test_redact_masks_the_middle() -> None:
    assert nbs.redact(AWS_KEY) == "AKIA" + "*" * 16
    assert nbs.redact("abc") == "***"                       # too short to keep any
    assert nbs.redact("-----BEGIN RSA PRIVATE KEY-----").startswith("-----BEGIN")


def test_shannon_entropy() -> None:
    assert nbs.shannon_entropy("aaaa") == 0.0
    assert nbs.shannon_entropy("") == 0.0
    assert round(nbs.shannon_entropy("abcd"), 2) == 2.0


# --- rules ------------------------------------------------------- #


@pytest.mark.parametrize(
    "text, rule",
    [
        (f'key = "{AWS_KEY}"', "aws-access-key-id"),
        (f'aws_secret_access_key = "{"A" * 40}"', "aws-secret-access-key"),
        (f'tok = "{GH_TOKEN}"', "github-token"),
        (f'tok = "glpat-{"x" * 20}"', "gitlab-token"),
        ('tok = "xoxb-123456789012-abcdefghijkl"', "slack-token"),
        (f't = "{DAPI}"', "databricks-pat"),
        ('k = "AIza' + "B" * 35 + '"', "google-api-key"),
        (
            'jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijkl"',
            "jwt",
        ),
        ("-----BEGIN OPENSSH PRIVATE KEY-----", "private-key"),
        ('u = "mysql://admin:hunter2pw@db.internal/prod"', "url-credentials"),
        ('password = "correcthorsebattery"', "generic-secret-assignment"),
        ('API_KEY: "abc123def456"', "generic-secret-assignment"),
    ],
)
def test_rule_detects(text: str, rule: str) -> None:
    hits = nbs.scan_text(text)
    assert rule in {h.rule for h in hits}, hits


@pytest.mark.parametrize(
    "text",
    [
        'password = "xxxxxxxx"',
        'password = "<your-password>"',
        'token = "changeme"',
        'secret = "example-value"',
        'api_key = "${API_KEY}"',
        'pwd = "{{ vault_password }}"',
        'url = "postgres://user:password@host/db"',   # 'password' is a placeholder
    ],
)
def test_placeholders_are_ignored(text: str) -> None:
    assert nbs.scan_text(text) == []


def test_allowlist_marker_suppresses_line() -> None:
    assert nbs.scan_text(f'k = "{AWS_KEY}"  # nbsecrets: allow') == []
    assert nbs.scan_text(f'k = "{AWS_KEY}"  # pragma: allowlist secret') == []
    assert nbs.scan_text(f'k = "{AWS_KEY}"') != []


def test_line_numbers_are_reported() -> None:
    text = f'a = 1\nb = 2\nkey = "{AWS_KEY}"\n'
    (finding,) = nbs.scan_text(text, path="x.py")
    assert finding.line == 3
    assert finding.location == "x.py:line 3"


def test_entropy_rule_is_opt_in() -> None:
    text = 'blob = "Xk9mQ2vLpR7wZ3nB6yT1aScDfGhJ4kMwEqUiOp"'
    assert nbs.scan_text(text) == []
    assert any(h.rule == "high-entropy-string" for h in nbs.scan_text(text, use_entropy=True))


# --- notebooks ------------------------------------------------- #


@pytest.fixture
def nb_with_secrets(tmp_path: Path) -> Path:
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "source": ["print('hello')\n"],
                "outputs": [{"output_type": "stream", "text": [f"{GH_TOKEN}\n"]}],
            },
            {
                "cell_type": "code",
                "source": [f"key = '{AWS_KEY}'\n"],
                "outputs": [],
            },
            {
                "cell_type": "markdown",
                "source": [f"connection string: `dapi{'f' * 32}`"],
            },
        ]
    }
    path = tmp_path / "leaky.ipynb"
    path.write_text(json.dumps(notebook))
    return path


def test_scan_notebook_source_and_outputs(nb_with_secrets: Path) -> None:
    findings = nbs.scan_path(nb_with_secrets)
    rules = sorted(f.rule for f in findings)
    assert rules == ["aws-access-key-id", "databricks-pat", "github-token"]

    out_finding = next(f for f in findings if f.in_output)
    assert out_finding.rule == "github-token"
    assert out_finding.cell == 0
    assert out_finding.location.endswith("cell 0:output")

    src_finding = next(f for f in findings if f.rule == "aws-access-key-id")
    assert src_finding.cell == 1
    assert src_finding.line == 1


def test_allowlist_marker_ignored_inside_outputs(tmp_path: Path) -> None:
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "source": ["x = 1\n"],
                "outputs": [{"output_type": "stream", "text": [f"{AWS_KEY}  # nbsecrets: allow\n"]}],
            }
        ]
    }
    path = tmp_path / "n.ipynb"
    path.write_text(json.dumps(notebook))
    # the marker only suppresses source lines, never output -- a leaked secret in
    # output is still a leak.
    assert nbs.scan_path(path)


def test_bad_notebook_json_falls_back_to_text_scan(tmp_path: Path) -> None:
    path = tmp_path / "broken.ipynb"
    path.write_text(f'{{ not json ... "{AWS_KEY}"')
    assert any(f.rule == "aws-access-key-id" for f in nbs.scan_path(path))


def test_exclude_glob(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text(f'k = "{AWS_KEY}"\n')
    (tmp_path / "vendored.py").write_text(f'k = "{AWS_KEY}"\n')
    findings = nbs.scan_path(tmp_path, exclude=["*vendored.py"])
    assert findings
    assert all("vendored.py" not in f.path for f in findings)


# --- CLI ----------------------------------------------------- #


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def test_cli_selftest_passes() -> None:
    assert _run("--selftest").returncode == 0


def test_cli_list_rules() -> None:
    out = _run("--list-rules").stdout
    assert "aws-access-key-id" in out
    assert "databricks-pat" in out


def test_cli_exit_codes(tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text("x = 1\n")
    assert _run(str(tmp_path)).returncode == 0

    (tmp_path / "bad.py").write_text(f'k = "{AWS_KEY}"\n')
    result = _run(str(tmp_path))
    assert result.returncode == 1
    assert "aws-access-key-id" in result.stdout
    assert "AKIA" in result.stdout
    assert AWS_KEY not in result.stdout  # redacted


def test_cli_json(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(f'k = "{AWS_KEY}"\n')
    payload = json.loads(_run(str(tmp_path), "--json").stdout)
    assert payload[0]["rule"] == "aws-access-key-id"
    assert payload[0]["line"] == 1
