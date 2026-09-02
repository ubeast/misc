#!/usr/bin/env python3
"""Extract selected cells from Jupyter (.ipynb) or Databricks (.py) notebooks.

Single file, standard library only. Usable as a CLI or importable as a module.

Use it to pull the SQL out of a notebook for linting, grab every cell with a
given tag, split a notebook into one file per cell, or dump a machine-readable
list of cells.

    $ python nb_extract.py etl.ipynb --lang sql --out etl.sql
    $ python nb_extract.py job.py --lang python --grep "spark.read"
    $ python nb_extract.py analysis.ipynb --format json

Supported inputs
----------------
* **Jupyter** ``.ipynb`` -- cell language comes from the kernel, overridden by a
  leading ``%%sql`` / ``%%bash`` / ... cell magic or a ``%sql`` / ``%md`` / ...
  line magic (the Databricks-on-Jupyter convention).
* **Databricks source export** (``.py``, ``.sql``, ``.scala``, ``.r``) -- cells
  are separated by ``# COMMAND ----------``; ``# MAGIC %sql`` blocks are decoded
  back to their real language and source.

Cell languages are normalized to: ``python``, ``sql``, ``markdown``, ``shell``,
``r``, ``scala``, ``raw``.

For developers
--------------
    from nb_extract import parse_notebook, select, render

    cells = parse_notebook(Path("etl.ipynb"))
    sql = select(cells, languages=["sql"])
    print(render(sql))                       # concatenated, with `-- cell N` markers

Run the built-in checks with:  python3 nb_extract.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Cell",
    "parse_notebook",
    "select",
    "render",
    "extension_for",
    "LANGUAGE_ALIASES",
]

# Normalize the many spellings of a language down to one token.
LANGUAGE_ALIASES: dict[str, str] = {
    "py": "python", "python": "python", "python3": "python", "ipython": "python",
    "sql": "sql",
    "md": "markdown", "markdown": "markdown",
    "sh": "shell", "bash": "shell", "shell": "shell", "%sh": "shell",
    "r": "r",
    "scala": "scala",
    "raw": "raw",
}

# Language magics that also imply "strip this first line from the source".
_MAGIC_LANGUAGES = {"sql", "md", "markdown", "sh", "bash", "shell", "r", "scala", "python", "py"}

_EXTENSIONS: dict[str, str] = {
    "python": ".py",
    "sql": ".sql",
    "markdown": ".md",
    "shell": ".sh",
    "r": ".r",
    "scala": ".scala",
    "raw": ".txt",
}

_COMMENT_PREFIX: dict[str, str] = {
    "python": "#", "shell": "#", "r": "#",
    "sql": "--",
    "scala": "//",
    "markdown": "",  # handled specially in render()
    "raw": "#",
}

_CELL_MAGIC_RE = re.compile(r"^\s*%%(\w+)")
_LINE_MAGIC_RE = re.compile(r"^\s*%(\w+)\s*$")


@dataclass
class Cell:
    """One notebook cell."""

    index: int
    language: str
    source: str
    cell_type: str = "code"
    tags: list[str] = field(default_factory=list)
    title: str | None = None


def _norm_lang(name: str, default: str = "python") -> str:
    return LANGUAGE_ALIASES.get(name.strip().lower().lstrip("%"), default)


def extension_for(language: str) -> str:
    """File extension (including the dot) for a normalized language name.

    >>> extension_for("sql")
    '.sql'
    >>> extension_for("python")
    '.py'
    """
    return _EXTENSIONS.get(language, ".txt")


# --- Jupyter .ipynb ---------------------------------------------------- #


def _notebook_language(data: dict) -> str:
    meta = data.get("metadata", {})
    for path in (("kernelspec", "language"), ("language_info", "name")):
        node: object = meta
        for key in path:
            node = node.get(key, {}) if isinstance(node, dict) else {}
        if isinstance(node, str) and node:
            return _norm_lang(node)
    return "python"


def _apply_cell_magic(source: str, default_lang: str) -> tuple[str, str]:
    """Return (language, source) after honoring a leading ``%%``/``%`` language magic.

    Only the recognized language magics in ``_MAGIC_LANGUAGES`` are acted on;
    the magic line is then removed from the source.
    """
    lines = source.splitlines()
    first_idx = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first_idx is None:
        return default_lang, source
    first = lines[first_idx]

    match = _CELL_MAGIC_RE.match(first) or _LINE_MAGIC_RE.match(first)
    if match and match.group(1).lower() in _MAGIC_LANGUAGES:
        language = _norm_lang(match.group(1), default_lang)
        remaining = lines[:first_idx] + lines[first_idx + 1:]
        return language, "\n".join(remaining).strip("\n")

    return default_lang, source


def _parse_ipynb(text: str) -> list[Cell]:
    data = json.loads(text)
    base_lang = _notebook_language(data)
    cells: list[Cell] = []
    for index, raw in enumerate(data.get("cells", [])):
        cell_type = raw.get("cell_type", "code")
        src = raw.get("source", "")
        source = "".join(src) if isinstance(src, list) else str(src)
        tags = list(raw.get("metadata", {}).get("tags", []) or [])

        if cell_type == "markdown":
            language = "markdown"
        elif cell_type == "raw":
            language = "raw"
        else:
            language, source = _apply_cell_magic(source, base_lang)
            if language == "markdown":
                cell_type = "markdown"

        cells.append(
            Cell(index=index, language=language, source=source.strip("\n"),
                 cell_type=cell_type, tags=tags)
        )
    return cells


# --- Databricks source export --------------------------------------- #


_DBX_HEADER_RE = re.compile(r"^(#|--|//) Databricks notebook source\s*$")
_DBTITLE_RE = re.compile(r'^(#|--|//) DBTITLE \d+,\s*"?(.*?)"?\s*$')


def _dbx_comment_token(text: str) -> str:
    first = text.splitlines()[0] if text.splitlines() else ""
    for token in ("--", "//", "#"):
        if first.startswith(f"{token} Databricks notebook source"):
            return token
    return "#"


def _parse_databricks(text: str) -> list[Cell]:
    token = _dbx_comment_token(text)
    esc = re.escape(token)
    command_re = re.compile(rf"^{esc} COMMAND -+\s*$")
    magic_re = re.compile(rf"^{esc} MAGIC(?: (.*))?$")
    base_lang = {"--": "sql", "//": "scala", "#": "python"}[token]

    chunks: list[list[str]] = [[]]
    for line in text.splitlines():
        if command_re.match(line):
            chunks.append([])
        else:
            chunks[-1].append(line)

    cells: list[Cell] = []
    for lines in chunks:
        body = [ln for ln in lines if not _DBX_HEADER_RE.match(ln)]
        title = None
        kept: list[str] = []
        for ln in body:
            dbt = _DBTITLE_RE.match(ln)
            if dbt:
                title = dbt.group(2) or None
            else:
                kept.append(ln)
        body = kept

        if not any(ln.strip() for ln in body):
            continue

        non_blank = [ln for ln in body if ln.strip()]
        is_magic_cell = bool(non_blank) and all(magic_re.match(ln) for ln in non_blank)

        if is_magic_cell:
            decoded = "\n".join(
                (magic_re.match(ln).group(1) or "") if magic_re.match(ln) else ""
                for ln in body
            )
            language, source = _apply_cell_magic(decoded, base_lang)
            cell_type = "markdown" if language == "markdown" else "code"
        else:
            language, source, cell_type = base_lang, "\n".join(body), "code"

        cells.append(
            Cell(
                index=len(cells),
                language=language,
                source=source.strip("\n"),
                cell_type=cell_type,
                title=title,
            )
        )
    return cells


# --- dispatch ------------------------------------------------------ #


def _looks_like_ipynb(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return False
    try:
        return isinstance(json.loads(text).get("cells"), list)
    except (ValueError, AttributeError):
        return False


def parse_notebook(source: "str | Path", fmt: str | None = None) -> list[Cell]:
    """Parse a notebook into a list of :class:`Cell`.

    ``source`` is a path or the notebook text itself. ``fmt`` forces
    ``"ipynb"`` or ``"databricks"``; by default it is detected from the content.

    Raises:
        ValueError: if the format cannot be determined / parsed.
    """
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8")
    else:
        text = source

    if fmt is None:
        fmt = "ipynb" if _looks_like_ipynb(text) else "databricks"

    if fmt == "ipynb":
        return _parse_ipynb(text)
    if fmt == "databricks":
        return _parse_databricks(text)
    raise ValueError(f"unknown notebook format: {fmt!r}")


# --- selection --------------------------------------------------- #


def parse_index_spec(spec: str) -> set[int]:
    """Expand a spec like ``"0,2,5-7"`` into ``{0, 2, 5, 6, 7}``.

    >>> sorted(parse_index_spec("0,2,5-7"))
    [0, 2, 5, 6, 7]
    """
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return out


def select(
    cells: list[Cell],
    *,
    languages: "list[str] | None" = None,
    tags: "list[str] | None" = None,
    grep: str | None = None,
    indices: "set[int] | None" = None,
    include_markdown: bool = False,
) -> list[Cell]:
    """Filter ``cells``.

    Selectors of different kinds are AND-ed; values within one kind are OR-ed.
    With no selectors, code cells are returned (markdown/raw only if
    ``include_markdown``).
    """
    norm_langs = {_norm_lang(l, l) for l in languages} if languages else None
    grep_re = re.compile(grep) if grep else None
    any_selector = any(x is not None for x in (languages, tags, grep, indices))

    out: list[Cell] = []
    for cell in cells:
        if not any_selector and not include_markdown and cell.cell_type != "code":
            continue
        if norm_langs is not None and cell.language not in norm_langs:
            continue
        if tags is not None and not (set(tags) & set(cell.tags)):
            continue
        if grep_re is not None and not grep_re.search(cell.source):
            continue
        if indices is not None and cell.index not in indices:
            continue
        out.append(cell)
    return out


# --- rendering ------------------------------------------------- #


def _separator(cell: Cell) -> str:
    label = f"cell {cell.index} [{cell.language}]"
    if cell.tags:
        label += f" tags={','.join(cell.tags)}"
    if cell.title:
        label += f" title={cell.title!r}"
    prefix = _COMMENT_PREFIX.get(cell.language, "#")
    if cell.language == "markdown":
        return f"<!-- {label} -->"
    return f"{prefix} {label}"


def render(cells: list[Cell], separators: bool = True) -> str:
    """Concatenate cell sources, optionally with a comment marker before each."""
    blocks: list[str] = []
    for cell in cells:
        if separators:
            blocks.append(f"{_separator(cell)}\n{cell.source}".rstrip())
        else:
            blocks.append(cell.source.rstrip())
    return "\n\n".join(blocks) + ("\n" if blocks else "")


# --- CLI ----------------------------------------------------- #


def _write_split(cells: list[Cell], directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for position, cell in enumerate(cells):
        name = f"{position:03d}_{cell.language}{extension_for(cell.language)}"
        path = directory / name
        path.write_text(cell.source + "\n", encoding="utf-8")
        written.append(path)
    return written


def _write_by_language(cells: list[Cell], directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    by_lang: dict[str, list[Cell]] = {}
    for cell in cells:
        by_lang.setdefault(cell.language, []).append(cell)
    written: list[Path] = []
    for language, group in sorted(by_lang.items()):
        path = directory / f"{language}{extension_for(language)}"
        path.write_text(render(group), encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("notebook", nargs="?", help="path to a .ipynb or Databricks-export notebook")
    parser.add_argument("--lang", action="append", metavar="LANG", help="keep cells of this language (repeatable)")
    parser.add_argument("--tag", action="append", metavar="TAG", help="keep cells with this tag (repeatable)")
    parser.add_argument("--grep", metavar="REGEX", help="keep cells whose source matches REGEX")
    parser.add_argument("--index", metavar="SPEC", help="keep cells by position, e.g. 0,2,5-7")
    parser.add_argument("--include-markdown", action="store_true", help="include markdown/raw cells when no other selector is given")
    parser.add_argument("--format", choices=["text", "json", "count"], default="text", help="output format (default: text)")
    parser.add_argument("--no-separators", action="store_true", help="omit the `# cell N` markers in text output")
    parser.add_argument("--out", metavar="FILE", help="write text output to FILE instead of stdout")
    parser.add_argument("--split", metavar="DIR", help="write one file per selected cell into DIR")
    parser.add_argument("--by-language", metavar="DIR", help="write one concatenated file per language into DIR")
    parser.add_argument("--format-hint", choices=["ipynb", "databricks"], help="force the input format")
    parser.add_argument("--selftest", action="store_true", help="run the built-in doctests and assertions and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.notebook:
        parser.error("provide a notebook path (or use --selftest)")

    path = Path(args.notebook)
    if not path.is_file():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 2
    try:
        cells = parse_notebook(path, fmt=args.format_hint)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    selected = select(
        cells,
        languages=args.lang,
        tags=args.tag,
        grep=args.grep,
        indices=parse_index_spec(args.index) if args.index else None,
        include_markdown=args.include_markdown,
    )

    if args.split:
        for written in _write_split(selected, Path(args.split)):
            print(written)
        return 0
    if args.by_language:
        for written in _write_by_language(selected, Path(args.by_language)):
            print(written)
        return 0

    if args.format == "count":
        counts: dict[str, int] = {}
        for cell in selected:
            counts[cell.language] = counts.get(cell.language, 0) + 1
        for language, count in sorted(counts.items()):
            print(f"{count:4d}  {language}")
        print(f"{len(selected):4d}  total")
        return 0

    if args.format == "json":
        payload = [
            {"index": c.index, "language": c.language, "cell_type": c.cell_type,
             "tags": c.tags, "title": c.title, "source": c.source}
            for c in selected
        ]
        output = json.dumps(payload, indent=2)
    else:
        output = render(selected, separators=not args.no_separators)

    if args.out:
        Path(args.out).write_text(output if output.endswith("\n") else output + "\n", encoding="utf-8")
        print(f"wrote {len(selected)} cell(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(output)
    return 0


def _selftest() -> int:
    import doctest

    failures, _ = doctest.testmod(verbose=False)

    ipynb = json.dumps(
        {
            "metadata": {"language_info": {"name": "python"}},
            "cells": [
                {"cell_type": "markdown", "source": ["# Title"]},
                {"cell_type": "code", "source": ["import os\n"], "metadata": {"tags": ["setup"]}},
                {"cell_type": "code", "source": ["%%sql\nSELECT 1\n"]},
                {"cell_type": "code", "source": ["%md\nsome notes\n"]},
            ],
        }
    )
    cells = parse_notebook(ipynb)
    assert [c.language for c in cells] == ["markdown", "python", "sql", "markdown"]
    assert cells[1].tags == ["setup"]
    assert cells[2].source == "SELECT 1"
    assert cells[3].cell_type == "markdown"

    sql_only = select(cells, languages=["sql"])
    assert len(sql_only) == 1 and sql_only[0].source == "SELECT 1"
    assert len(select(cells, tags=["setup"])) == 1
    assert len(select(cells, grep="SELECT")) == 1
    assert len(select(cells, indices={0})) == 1
    assert len(select(cells)) == 2           # code cells only (2 markdown excluded)
    assert len(select(cells, include_markdown=True)) == 4

    dbx = (
        "# Databricks notebook source\n"
        "# MAGIC %md\n"
        "# MAGIC # Heading\n"
        "\n"
        "# COMMAND ----------\n"
        "\n"
        '# DBTITLE 1,"Load"\n'
        "df = spark.read.table('t')\n"
        "\n"
        "# COMMAND ----------\n"
        "\n"
        "# MAGIC %sql\n"
        "# MAGIC SELECT count(*) FROM t\n"
    )
    dcells = parse_notebook(dbx)
    assert [c.language for c in dcells] == ["markdown", "python", "sql"], [c.language for c in dcells]
    assert dcells[0].source == "# Heading"
    assert dcells[1].title == "Load"
    assert "spark.read" in dcells[1].source
    assert dcells[2].source == "SELECT count(*) FROM t"

    assert "-- cell" in render(select(dcells, languages=["sql"]))
    assert render(select(dcells, languages=["sql"]), separators=False).strip() == "SELECT count(*) FROM t"
    assert parse_index_spec("1,3-4") == {1, 3, 4}
    assert extension_for("scala") == ".scala"

    if failures:
        print(f"{failures} doctest failure(s)", file=sys.stderr)
        return 1
    print("selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
