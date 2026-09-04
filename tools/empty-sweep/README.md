# empty-sweep

Find (and optionally delete) empty files in a folder — including notebooks
that only *look* non-empty because Databricks or Jupyter wrote scaffolding
into them.

One file: [`empty_sweep.py`](empty_sweep.py), standard library only. Copy it
wherever you need it — no install.

```bash
$ python empty_sweep.py . -r
blank.txt
notebooks/scratch.ipynb
jobs/untitled.py

3 empty file(s) found (dry run -- pass --delete to remove them)
```

## What counts as "empty"

- Any **zero-byte** file.
- A **`.ipynb`** notebook with no cells, or where every cell's source is
  blank once whitespace is stripped.
- A **Databricks source-export** notebook (`.py` / `.sql` / `.scala` / `.r`
  starting with `# Databricks notebook source`, or the `--`/`//` variant)
  where every cell is blank once the header line, `# COMMAND ----------`
  separators, `# DBTITLE` lines, and a bare cell-language switch
  (`# MAGIC %sql` with nothing else on the line) are discounted — that's
  Databricks scaffolding, not something a person wrote.
- Any other **text file** whose content is whitespace-only. Pass
  `--bytes-only` to turn this off and match true zero-byte files only.

A file that isn't valid UTF-8 (binary) and isn't zero bytes is **never**
treated as empty — there's no way to tell "no content" from "content this
tool can't read," so it errs toward keeping the file. The same goes for a
`.ipynb` file that isn't valid JSON.

Directories are scanned **top-level only** unless you pass `-r`/`--recursive`.
`.git`, `__pycache__`, `.ipynb_checkpoints`, `venv`, `.venv`, and
`node_modules` are skipped by default; add more with `--exclude`.

Deletion is opt-in — without `--delete` this only lists what it found.

## Use as a library

```python
from pathlib import Path
from empty_sweep import find_empty, delete_files

report = find_empty([Path("workspace")], recursive=True)
report.empty     # -> [Path('workspace/blank.txt'), ...]
report.errors    # -> [('workspace/nope', 'path does not exist')]

deleted, errors = delete_files(report.empty)
```

## Use from the command line

```bash
python empty_sweep.py                    # dry run, current dir, top-level only
python empty_sweep.py . -r               # dry run, recurse into subfolders
python empty_sweep.py . -r --delete      # actually delete what was found
python empty_sweep.py . --bytes-only     # only match true zero-byte files
python empty_sweep.py . --exclude scratch  # skip an extra directory name
python empty_sweep.py . --json           # structured report (and delete results)
python empty_sweep.py --selftest
```

Exit code: `0` normally · `1` if a given path doesn't exist or a delete failed.

## Using this inside Databricks

This tool walks a real filesystem path — it doesn't call the Workspace API.
That works directly against a **Repos-backed** folder (or any workspace path
exposed as workspace files), where notebooks are ordinary files on disk. A
notebook that only exists in the legacy (non-Repos) workspace isn't a
filesystem path at all; export it first (e.g. via `dbutils.workspace.export`)
if you need to check it with this tool.

## Test

```bash
uv run pytest tools/empty-sweep
```
