#!/usr/bin/env python3
"""Scan notebooks and scripts for hard-coded secrets.

Single file, standard library only. Usable as a CLI or importable as a module.

Meant as a cheap pre-commit / CI check for the mistake everyone makes once:
pasting a token, password, or connection string straight into a notebook cell
(or leaving one in a cell's *output*, which ``.ipynb`` files keep).

    $ python nb_secrets.py notebooks/
    notebooks/etl.ipynb:cell 4:line 2: [aws-access-key-id] AKIA****************
    notebooks/etl.ipynb:cell 9:output:     [private-key] -----BEGIN RSA PRIVATE KEY-----
    2 finding(s)

What it looks at
----------------
* ``.py`` files -- line by line.
* ``.ipynb`` files -- every code/markdown cell source, **and** every text output
  (``stream`` text, ``text/plain`` data, execute-result data). Outputs leak
  secrets constantly and diff tools hide them.

Rules
-----
Detects: AWS access key IDs / secret keys, GitHub & GitLab tokens, Slack tokens
and webhooks, Google API keys, Databricks PATs (``dapi...``), JWTs, PEM private
keys, URLs with an embedded ``user:password@``, and generic
``password=/token=/api_key=`` assignments to a literal (placeholders like
``"xxxx"`` / ``"<your-token>"`` are ignored). Optionally, high-entropy strings
with ``--entropy``.

Suppressing a match
-------------------
Put ``# nbsecrets: allow`` or ``# pragma: allowlist secret`` on the same line.
Exclude whole paths with ``--exclude GLOB`` (repeatable).

For developers
--------------
    from nb_secrets import scan_path, scan_text, Finding, RULES

    for finding in scan_path(Path("notebooks")):
        print(finding.location, finding.rule, finding.redacted)

Run the built-in checks with:  python3 nb_secrets.py --selftest
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Finding",
    "Rule",
    "RULES",
    "scan_text",
    "scan_path",
    "redact",
    "shannon_entropy",
    "ALLOW_MARKERS",
]

ALLOW_MARKERS: tuple[str, ...] = ("nbsecrets: allow", "pragma: allowlist secret")

# Values that look like assignments but are obviously not real secrets.
_PLACEHOLDER_RE = re.compile(
    r"""^(?:
        |none|null|true|false
        |x{3,}|\*{3,}|\.{3,}|-{3,}
        |password|passwd|pass|secret|admin|root|user(?:name)?|postgres|changeit
        |your[-_ ].*|my[-_ ].*|some[-_ ].*
        |example.*|sample.*|dummy.*|test.*|fake.*|changeme.*|placeholder.*
        |redacted.*|todo.*|tbd.*
        |<[^>]+>|\{\{[^}]+\}\}|\$\{[^}]+\}|%\([^)]+\)s|\$[a-z_]+
    )$""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class Rule:
    """A single detection rule."""

    name: str
    pattern: re.Pattern[str]
    description: str
    check_placeholder: bool = False
    """If True, skip the match when its captured group looks like a placeholder."""


def _r(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pattern, flags)


RULES: tuple[Rule, ...] = (
    Rule(
        "aws-access-key-id",
        _r(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|A3T[A-Z0-9])[A-Z0-9]{16}\b"),
        "AWS access key ID",
    ),
    Rule(
        "aws-secret-access-key",
        _r(r"(?i)aws_secret_access_key['\"]?\s*[:=]\s*['\"]([A-Za-z0-9/+=]{40})['\"]"),
        "AWS secret access key assignment",
    ),
    Rule(
        "github-token",
        _r(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
        "GitHub personal access / OAuth token",
    ),
    Rule(
        "gitlab-token",
        _r(r"\bglpat-[A-Za-z0-9_-]{20}\b"),
        "GitLab personal access token",
    ),
    Rule(
        "slack-token",
        _r(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
        "Slack API token",
    ),
    Rule(
        "slack-webhook",
        _r(r"https://hooks\.slack\.com/services/[A-Za-z0-9+/]{40,}"),
        "Slack incoming webhook URL",
    ),
    Rule(
        "google-api-key",
        _r(r"\bAIza[A-Za-z0-9_-]{35}\b"),
        "Google API key",
    ),
    Rule(
        "databricks-pat",
        _r(r"\bdapi[0-9a-f]{32}(?:-\d)?\b"),
        "Databricks personal access token",
    ),
    Rule(
        "jwt",
        _r(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "JSON Web Token",
    ),
    Rule(
        "private-key",
        _r(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
        "PEM private key block",
    ),
    Rule(
        "url-credentials",
        _r(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:([^\s:/@]{3,})@[^\s/]+"),
        "URL with embedded username:password",
        check_placeholder=True,
    ),
    Rule(
        "generic-secret-assignment",
        _r(
            r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key"
            r"|client[_-]?secret|auth[_-]?token|private[_-]?key)\b"
            r"\s*[:=]\s*['\"]([^'\"\n]{6,})['\"]"
        ),
        "hard-coded password/token/api_key assignment",
        check_placeholder=True,
    ),
)

_HIGH_ENTROPY_CANDIDATE_RE = _r(r"['\"]([A-Za-z0-9+/=_\-]{20,})['\"]")
_ENTROPY_THRESHOLD = 4.0


@dataclass(frozen=True)
class Finding:
    """One suspected secret."""

    path: str
    cell: int | None
    """0-based code/markdown cell index for notebooks, else ``None``."""
    line: int | None
    """1-based line within the cell/file, or ``None`` for an output location."""
    in_output: bool
    rule: str
    description: str
    redacted: str

    @property
    def location(self) -> str:
        parts = [self.path]
        if self.cell is not None:
            parts.append(f"cell {self.cell}")
        if self.in_output:
            parts.append("output")
        elif self.line is not None:
            parts.append(f"line {self.line}")
        return ":".join(parts)


def shannon_entropy(text: str) -> float:
    """Bits-per-character Shannon entropy of ``text``.

    >>> shannon_entropy("aaaa")
    0.0
    >>> round(shannon_entropy("abcd"), 2)
    2.0
    """
    if not text:
        return 0.0
    counts = {ch: text.count(ch) for ch in set(text)}
    length = len(text)
    bits = -sum((n / length) * math.log2(n / length) for n in counts.values())
    return bits or 0.0  # normalize -0.0 -> 0.0


def redact(secret: str, keep_start: int = 4, keep_end: int = 0) -> str:
    """Mask the middle of ``secret``.

    >>> redact("AKIAIOSFODNN7EXAMPLE")
    'AKIA****************'
    >>> redact("-----BEGIN RSA PRIVATE KEY-----")
    '-----BEGIN RSA PRIVATE KEY-----'
    """
    secret = secret.strip()
    if secret.startswith("-----BEGIN"):
        return secret  # not sensitive on its own; the key body is on later lines
    if len(secret) <= keep_start + keep_end:
        return "*" * len(secret)
    masked = "*" * (len(secret) - keep_start - keep_end)
    return secret[:keep_start] + masked + (secret[len(secret) - keep_end:] if keep_end else "")


def _line_is_allowlisted(line: str) -> bool:
    return any(marker in line for marker in ALLOW_MARKERS)


def _looks_like_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(value.strip()))


def _iter_rule_hits(text: str, use_entropy: bool):
    """Yield ``(rule_name, description, matched_secret)`` for one chunk of text."""
    for rule in RULES:
        for match in rule.pattern.finditer(text):
            captured = match.group(1) if match.groups() else match.group(0)
            if rule.check_placeholder and _looks_like_placeholder(captured):
                continue
            yield rule.name, rule.description, captured
    if use_entropy:
        for match in _HIGH_ENTROPY_CANDIDATE_RE.finditer(text):
            candidate = match.group(1)
            if _looks_like_placeholder(candidate):
                continue
            if shannon_entropy(candidate) >= _ENTROPY_THRESHOLD:
                yield "high-entropy-string", "high-entropy string literal", candidate


def scan_text(
    text: str,
    *,
    path: str = "<text>",
    cell: int | None = None,
    in_output: bool = False,
    use_entropy: bool = False,
) -> list[Finding]:
    """Scan a block of text (one file, or one notebook cell/output)."""
    findings: list[Finding] = []
    lines = text.splitlines() or [text]
    for lineno, line in enumerate(lines, start=1):
        if not in_output and _line_is_allowlisted(line):
            continue
        for rule_name, description, secret in _iter_rule_hits(line, use_entropy):
            findings.append(
                Finding(
                    path=path,
                    cell=cell,
                    line=None if in_output else lineno,
                    in_output=in_output,
                    rule=rule_name,
                    description=description,
                    redacted=redact(secret),
                )
            )
    return findings


# --- notebook handling ------------------------------------------------- #


def _output_texts(cell: dict) -> list[str]:
    texts: list[str] = []
    for output in cell.get("outputs", []) or []:
        if "text" in output:
            src = output["text"]
            texts.append("".join(src) if isinstance(src, list) else str(src))
        data = output.get("data", {})
        for mime, value in data.items():
            if mime.startswith("text/") or mime == "application/json":
                texts.append("".join(value) if isinstance(value, list) else str(value))
    return texts


def _scan_ipynb(text: str, path: str, use_entropy: bool) -> list[Finding]:
    data = json.loads(text)
    findings: list[Finding] = []
    for index, cell in enumerate(data.get("cells", [])):
        src = cell.get("source", "")
        source = "".join(src) if isinstance(src, list) else str(src)
        findings.extend(
            scan_text(source, path=path, cell=index, use_entropy=use_entropy)
        )
        for output_text in _output_texts(cell):
            findings.extend(
                scan_text(
                    output_text, path=path, cell=index, in_output=True,
                    use_entropy=use_entropy,
                )
            )
    return findings


def scan_path(
    target: Path,
    *,
    exclude: "list[str] | None" = None,
    use_entropy: bool = False,
) -> list[Finding]:
    """Scan a file or directory tree. Returns findings in file/cell/line order."""
    exclude = exclude or []
    findings: list[Finding] = []

    if target.is_file():
        files = [target]
    else:
        files = sorted(
            p for p in target.rglob("*")
            if p.is_file() and p.suffix in {".py", ".ipynb"}
        )

    for file in files:
        rel = str(file)
        if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(file.name, pat) for pat in exclude):
            continue
        try:
            content = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if file.suffix == ".ipynb":
            try:
                findings.extend(_scan_ipynb(content, rel, use_entropy))
            except (ValueError, json.JSONDecodeError):
                findings.extend(scan_text(content, path=rel, use_entropy=use_entropy))
        else:
            findings.extend(scan_text(content, path=rel, use_entropy=use_entropy))
    return findings


# --- CLI ------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="*", default=["."], help="files or directories to scan (default: .)")
    parser.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip paths matching GLOB (repeatable)")
    parser.add_argument("--entropy", action="store_true", help="also flag high-entropy string literals (noisier)")
    parser.add_argument("--json", action="store_true", help="emit findings as a JSON array")
    parser.add_argument("--list-rules", action="store_true", help="print the detection rules and exit")
    parser.add_argument("--selftest", action="store_true", help="run the built-in doctests and assertions and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.list_rules:
        for rule in RULES:
            print(f"{rule.name:28s} {rule.description}")
        return 0

    all_findings: list[Finding] = []
    for raw in args.paths:
        path = Path(raw)
        if not path.exists():
            print(f"warning: no such path: {path}", file=sys.stderr)
            continue
        all_findings.extend(scan_path(path, exclude=args.exclude, use_entropy=args.entropy))

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "path": f.path, "cell": f.cell, "line": f.line,
                        "in_output": f.in_output, "rule": f.rule,
                        "description": f.description, "redacted": f.redacted,
                        "location": f.location,
                    }
                    for f in all_findings
                ],
                indent=2,
            )
        )
    else:
        for f in all_findings:
            print(f"{f.location}: [{f.rule}] {f.redacted}")
        print(f"{len(all_findings)} finding(s)", file=sys.stderr)

    return 1 if all_findings else 0


def _selftest() -> int:
    import doctest
    import tempfile

    failures, _ = doctest.testmod(verbose=False)

    assert redact("AKIAIOSFODNN7EXAMPLE") == "AKIA****************"
    assert shannon_entropy("aaaa") == 0.0

    hits = scan_text('aws_key = "AKIAIOSFODNN7EXAMPLE"')
    assert [h.rule for h in hits] == ["aws-access-key-id"], hits

    assert scan_text('token = "ghp_' + "a" * 36 + '"')[0].rule == "github-token"
    assert scan_text('password = "hunter2secret"')[0].rule == "generic-secret-assignment"
    assert scan_text('password = "xxxxxxx"') == []            # placeholder
    assert scan_text('password = "<your-password-here>"') == []
    assert scan_text('token = "realtokenvalue123"  # nbsecrets: allow') == []
    assert scan_text('url = "postgres://user:s3cr3tpw@host:5432/db"')[0].rule == "url-credentials"
    assert scan_text('t = "dapi' + "0" * 32 + '"')[0].rule == "databricks-pat"
    assert scan_text("-----BEGIN RSA PRIVATE KEY-----")[0].rule == "private-key"

    no_entropy = scan_text('blob = "aGVsbG8gd29ybGQgdGhpcyBpcyBub3Qgc2VjcmV0"')
    assert no_entropy == []
    with_entropy = scan_text('key = "Xk9mQ2vLpR7wZ3nB6yT1aScDfGhJ4kMwEqUiOp"', use_entropy=True)
    assert any(h.rule == "high-entropy-string" for h in with_entropy), with_entropy

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "clean.py").write_text("x = 1\n")
        (root / "bad.py").write_text('SLACK = "x  "\nAPI = "AKIAIOSFODNN7EXAMPLE"\n')
        notebook = {
            "cells": [
                {"cell_type": "code", "source": ["print('ok')\n"], "outputs": [
                    {"output_type": "stream", "text": ["ghp_" + "b" * 36 + "\n"]}
                ]},
                {"cell_type": "code", "source": ["pat = 'dapi" + "f" * 32 + "'\n"], "outputs": []},
            ]
        }
        (root / "nb.ipynb").write_text(json.dumps(notebook))

        findings = scan_path(root)
        rules = sorted(f.rule for f in findings)
        assert rules == ["aws-access-key-id", "databricks-pat", "github-token"], rules
        output_finding = next(f for f in findings if f.in_output)
        assert output_finding.location.endswith("cell 0:output")

        assert scan_path(root, exclude=["*bad.py"]) and all(
            "bad.py" not in f.path for f in scan_path(root, exclude=["*bad.py"])
        )

    if failures:
        print(f"{failures} doctest failure(s)", file=sys.stderr)
        return 1
    print("selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
