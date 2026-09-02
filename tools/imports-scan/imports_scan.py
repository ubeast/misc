#!/usr/bin/env python3
"""Scan .py and .ipynb files for imports; separate stdlib / third-party / local.

Single file, standard library only. Usable as a CLI or importable as a module.

The point: figure out what a script or notebook (or a whole folder of them)
actually depends on, so you can build a ``requirements.txt`` or a Databricks
cluster library list without reading every file by hand.

    $ python imports_scan.py analysis/
    delta-spark
    pandas
    requests
    scikit-learn

What it does
------------
* Walks the given paths (files or directories), reading ``*.py`` and ``*.ipynb``.
* Collects every ``import x`` / ``from x import y`` via the ``ast`` module, with a
  regex fallback for files that do not parse.
* In notebooks, ignores ``%``/``!`` magic lines but *does* pick up
  ``%pip install ...`` / ``!pip install ...`` (including Databricks
  ``# MAGIC %pip install ...``) as explicit dependencies.
* Classifies each imported top-level module:
    - **relative**   -- ``from . import x``
    - **local**      -- a module/package that exists inside the scanned tree
    - **stdlib**     -- in ``sys.stdlib_module_names``
    - **third_party** -- everything else
* Maps well-known import names to their PyPI distribution
  (``cv2`` -> ``opencv-python``, ``sklearn`` -> ``scikit-learn``, ...).

Limitations
-----------
* stdlib classification reflects *this* interpreter's version.
* "local" detection finds ``foo.py`` and packages with ``__init__.py``; PEP 420
  namespace packages (a bare directory) are missed.
* The import-name -> distribution map is best-effort; unmapped names are emitted
  verbatim.

For developers
--------------
    from imports_scan import scan

    result = scan([Path("analysis")])
    result.requirements()          # -> ['pandas', 'requests', ...]
    result.third_party             # -> {'pandas': ['analysis/a.ipynb', ...], ...}
    result.stdlib, result.local, result.relative, result.errors

Run the built-in checks with:  python3 imports_scan.py --selftest
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "scan",
    "ScanResult",
    "classify_module",
    "distribution_for",
    "IMPORT_TO_DISTRIBUTION",
    "DEFAULT_EXCLUDE_DIRS",
]

DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
        ".ipynb_checkpoints", ".mypy_cache", ".pytest_cache", ".tox",
        "node_modules", "build", "dist", ".eggs", "site-packages",
    }
)

SOURCE_SUFFIXES: frozenset[str] = frozenset({".py", ".ipynb"})

# Best-effort import-name -> PyPI distribution name for the common mismatches.
IMPORT_TO_DISTRIBUTION: dict[str, str] = {
    "attr": "attrs",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "Crypto": "pycryptodome",
    "Cryptodome": "pycryptodomex",
    "dateutil": "python-dateutil",
    "delta": "delta-spark",
    "dns": "dnspython",
    "docx": "python-docx",
    "dotenv": "python-dotenv",
    "fitz": "PyMuPDF",
    "grpc": "grpcio",
    "jwt": "PyJWT",
    "magic": "python-magic",
    "MySQLdb": "mysqlclient",
    "nacl": "PyNaCl",
    "OpenSSL": "pyOpenSSL",
    "pkg_resources": "setuptools",
    "PIL": "Pillow",
    "pptx": "python-pptx",
    "psycopg2": "psycopg2-binary",
    "serial": "pyserial",
    "skimage": "scikit-image",
    "sklearn": "scikit-learn",
    "slugify": "python-slugify",
    "snowflake": "snowflake-connector-python",
    "usb": "pyusb",
    "win32api": "pywin32",
    "win32com": "pywin32",
    "yaml": "PyYAML",
}

_MAGIC_LINE_RE = re.compile(r"^\s*[%!]")
# %pip install / !pip install / # MAGIC %pip install  (also conda)
_PIP_INSTALL_RE = re.compile(
    r"(?:#\s*MAGIC\s+)?[%!]\s*(?:pip|conda|mamba|micromamba)\s+install\s+(?P<args>.+)$",
    re.MULTILINE,
)
_IMPORT_FALLBACK_RE = re.compile(
    r"^[ \t]*(?:import|from)[ \t]+(?P<dots>\.*)(?P<module>[A-Za-z_][\w.]*)?",
    re.MULTILINE,
)
# Split a pip requirement spec off its version / extras / markers.
_DIST_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+")


@dataclass
class ScanResult:
    """Outcome of :func:`scan`.

    Each ``dict`` maps a top-level import name to the sorted list of files it was
    seen in. ``pip_installs`` maps a raw requirement spec to its files.
    """

    third_party: dict[str, list[str]] = field(default_factory=dict)
    stdlib: dict[str, list[str]] = field(default_factory=dict)
    local: dict[str, list[str]] = field(default_factory=dict)
    relative: dict[str, list[str]] = field(default_factory=dict)
    pip_installs: dict[str, list[str]] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)
    files_scanned: int = 0

    def requirements(self) -> list[str]:
        """Sorted, de-duplicated list of distribution names to install.

        Third-party imports mapped through :data:`IMPORT_TO_DISTRIBUTION`, plus
        the package names from any ``pip install`` lines found in notebooks.
        """
        names = {distribution_for(name) for name in self.third_party}
        for spec in self.pip_installs:
            match = _DIST_NAME_RE.match(spec)
            if match:
                names.add(match.group(0))
        return sorted(names, key=str.lower)


def distribution_for(import_name: str) -> str:
    """Return the PyPI distribution name for a top-level import name.

    >>> distribution_for("sklearn")
    'scikit-learn'
    >>> distribution_for("pandas")
    'pandas'
    """
    return IMPORT_TO_DISTRIBUTION.get(import_name, import_name)


def classify_module(
    top: str, level: int, local_names: frozenset[str]
) -> str:
    """Return one of ``relative`` / ``local`` / ``stdlib`` / ``third_party``.

    >>> classify_module("os", 0, frozenset())
    'stdlib'
    >>> classify_module("numpy", 0, frozenset())
    'third_party'
    >>> classify_module("mymod", 0, frozenset({"mymod"}))
    'local'
    >>> classify_module("anything", 1, frozenset())
    'relative'
    """
    if level > 0:
        return "relative"
    if top in local_names:
        return "local"
    if top in sys.stdlib_module_names:
        return "stdlib"
    return "third_party"


# --- extraction ---------------------------------------------------------- #


def _imports_from_ast(source: str) -> list[tuple[str, int]]:
    """Return ``(top_level_module, relative_level)`` pairs, or raise SyntaxError.

    ``from . import x`` yields ``("", 1)``.
    """
    tree = ast.parse(source)
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name.split(".")[0], 0))
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            found.append((module, node.level))
    return found


def _imports_from_regex(source: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for match in _IMPORT_FALLBACK_RE.finditer(source):
        dots = match.group("dots") or ""
        module = match.group("module") or ""
        found.append((module.split(".")[0], len(dots)))
    return found


def _extract_imports(source: str) -> list[tuple[str, int]]:
    try:
        return _imports_from_ast(source)
    except SyntaxError:
        return _imports_from_regex(source)


def _pip_install_specs(raw_text: str) -> list[str]:
    """Package specs from ``pip/conda install`` lines (magic or Databricks MAGIC)."""
    specs: list[str] = []
    for match in _PIP_INSTALL_RE.finditer(raw_text):
        for token in match.group("args").split():
            if token.startswith("-"):
                continue
            if token in {"install"}:  # defensive
                continue
            if _DIST_NAME_RE.match(token):
                specs.append(token.strip("\"'"))
    return specs


def _strip_magics(source: str) -> str:
    return "\n".join(
        "" if _MAGIC_LINE_RE.match(line) else line
        for line in source.splitlines()
    )


def _notebook_code_and_text(path: Path) -> tuple[list[str], str]:
    """Return (list of code-cell sources, whole raw text) for an .ipynb file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    cells = data.get("cells", [])
    code_sources: list[str] = []
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        src = cell.get("source", "")
        code_sources.append("".join(src) if isinstance(src, list) else str(src))
    return code_sources, "\n".join(code_sources)


# --- local-name discovery ---------------------------------------------- #


def _discover_local_names(files: list[Path], roots: list[Path]) -> frozenset[str]:
    names: set[str] = set()
    for path in files:
        if path.suffix == ".py":
            names.add(path.stem)
        # a package: directory containing __init__.py
        if path.name == "__init__.py":
            names.add(path.parent.name)
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            names.add(root.stem)
    names.discard("__init__")
    return frozenset(names)


def _iter_source_files(
    root: Path, exclude_dirs: frozenset[str]
) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix in SOURCE_SUFFIXES else []
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if path.suffix not in SOURCE_SUFFIXES:
            continue
        if any(part in exclude_dirs for part in path.parts):
            continue
        out.append(path)
    return out


# --- top-level scan --------------------------------------------------- #


def scan(
    paths: list[Path],
    *,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
) -> ScanResult:
    """Scan ``paths`` (files and/or directories) and return a :class:`ScanResult`."""
    roots = [Path(p) for p in paths]
    files: list[Path] = []
    result = ScanResult()

    for root in roots:
        if not root.exists():
            result.errors.append((str(root), "path does not exist"))
            continue
        files.extend(_iter_source_files(root, exclude_dirs))

    files = sorted(set(files))
    local_names = _discover_local_names(files, roots)

    buckets = {
        "third_party": defaultdict(set),
        "stdlib": defaultdict(set),
        "local": defaultdict(set),
        "relative": defaultdict(set),
    }
    pip_installs: dict[str, set[str]] = defaultdict(set)

    for path in files:
        label = str(path)
        try:
            if path.suffix == ".ipynb":
                code_sources, raw_text = _notebook_code_and_text(path)
                imports: list[tuple[str, int]] = []
                for cell_source in code_sources:
                    imports.extend(_extract_imports(_strip_magics(cell_source)))
                for spec in _pip_install_specs(raw_text):
                    pip_installs[spec].add(label)
            else:
                text = path.read_text(encoding="utf-8")
                imports = _extract_imports(text)
                for spec in _pip_install_specs(text):
                    pip_installs[spec].add(label)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result.errors.append((label, f"{type(exc).__name__}: {exc}"))
            continue

        result.files_scanned += 1
        for top, level in imports:
            category = classify_module(top, level, local_names)
            key = top if top else "."
            buckets[category][key].add(label)

    result.third_party = {k: sorted(v) for k, v in sorted(buckets["third_party"].items())}
    result.stdlib = {k: sorted(v) for k, v in sorted(buckets["stdlib"].items())}
    result.local = {k: sorted(v) for k, v in sorted(buckets["local"].items())}
    result.relative = {k: sorted(v) for k, v in sorted(buckets["relative"].items())}
    result.pip_installs = {k: sorted(v) for k, v in sorted(pip_installs.items())}
    return result


# --- CLI ------------------------------------------------------------- #


def _print_report(result: ScanResult, args: argparse.Namespace) -> None:
    if args.json:
        print(
            json.dumps(
                {
                    "requirements": result.requirements(),
                    "third_party": result.third_party,
                    "stdlib": result.stdlib,
                    "local": result.local,
                    "relative": result.relative,
                    "pip_installs": result.pip_installs,
                    "errors": [{"file": f, "error": e} for f, e in result.errors],
                    "files_scanned": result.files_scanned,
                },
                indent=2,
            )
        )
        return

    for dist in result.requirements():
        if args.with_files:
            origins = _origin_files(result, dist)
            print(f"{dist}  # {', '.join(origins)}")
        else:
            print(dist)

    if args.all:
        print()
        print(f"# stdlib ({len(result.stdlib)}): " + " ".join(result.stdlib))
        print(f"# local ({len(result.local)}): " + " ".join(result.local))
        if result.relative:
            print(f"# relative imports in: "
                  + " ".join(sorted({f for files in result.relative.values() for f in files})))


def _origin_files(result: ScanResult, dist: str) -> list[str]:
    origins: set[str] = set()
    for name, files in result.third_party.items():
        if distribution_for(name) == dist:
            origins.update(files)
    for spec, files in result.pip_installs.items():
        match = _DIST_NAME_RE.match(spec)
        if match and match.group(0) == dist:
            origins.update(files)
    return sorted(origins)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="*", default=["."], help="files or directories to scan (default: .)")
    parser.add_argument("--exclude", action="append", default=[], metavar="DIR", help="extra directory name to skip (repeatable)")
    parser.add_argument("--json", action="store_true", help="emit the full structured report as JSON")
    parser.add_argument("--all", action="store_true", help="also list stdlib / local / relative imports (as comments)")
    parser.add_argument("--with-files", action="store_true", help="annotate each requirement with the files it came from")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any file could not be read/parsed")
    parser.add_argument("--selftest", action="store_true", help="run the built-in doctests and assertions and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()

    exclude = DEFAULT_EXCLUDE_DIRS | set(args.exclude)
    result = scan([Path(p) for p in args.paths], exclude_dirs=frozenset(exclude))

    for file, error in result.errors:
        print(f"warning: {file}: {error}", file=sys.stderr)

    _print_report(result, args)

    if args.strict and result.errors:
        return 1
    return 0


def _selftest() -> int:
    import doctest
    import tempfile

    failures, _ = doctest.testmod(verbose=False)

    assert distribution_for("sklearn") == "scikit-learn"
    assert distribution_for("numpy") == "numpy"
    assert classify_module("os", 0, frozenset()) == "stdlib"
    assert classify_module("numpy", 0, frozenset()) == "third_party"
    assert classify_module("x", 2, frozenset()) == "relative"

    assert _extract_imports("import os, sys\nfrom pandas import DataFrame") == [
        ("os", 0), ("sys", 0), ("pandas", 0),
    ]
    assert ("", 1) in _extract_imports("from . import helpers")
    assert _extract_imports("def broken(:\n  import numpy") == [("numpy", 0)]  # regex fallback
    assert _pip_install_specs("# MAGIC %pip install rich==13.0 pandas") == ["rich==13.0", "pandas"]
    assert _pip_install_specs("!pip install -q boto3") == ["boto3"]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "helpers.py").write_text("X = 1\n")
        (root / "job.py").write_text(
            "import os\nimport pandas as pd\nimport helpers\nfrom . import sib\n"
        )
        notebook = {
            "cells": [
                {"cell_type": "code", "source": ["%pip install requests\n", "import requests\n"]},
                {"cell_type": "code", "source": "import numpy as np\nimport sklearn\n"},
                {"cell_type": "markdown", "source": ["# title"]},
            ]
        }
        (root / "nb.ipynb").write_text(json.dumps(notebook))
        (root / ".ipynb_checkpoints").mkdir()
        (root / ".ipynb_checkpoints" / "nb-checkpoint.ipynb").write_text('{"cells": [{"cell_type":"code","source":["import evil\n"]}]}')

        result = scan([root])
        assert result.files_scanned == 3, result.files_scanned  # checkpoint skipped
        assert result.requirements() == ["numpy", "pandas", "requests", "scikit-learn"], result.requirements()
        assert set(result.stdlib) == {"os"}
        assert set(result.local) == {"helpers"}
        assert "." in result.relative
        assert "evil" not in {n for n in result.third_party}

        result2 = scan([root / "does_not_exist.py"])
        assert result2.errors

    if failures:
        print(f"{failures} doctest failure(s)", file=sys.stderr)
        return 1
    print("selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
