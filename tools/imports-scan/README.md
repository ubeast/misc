# imports-scan

Find out what a script or notebook (or a whole folder of them) actually imports,
and turn that into a dependency list.

One file: [`imports_scan.py`](imports_scan.py), standard library only. Copy it
wherever you need it — no install.

```bash
$ python imports_scan.py analysis/
delta-spark
pandas
requests
scikit-learn
```

## What it does

- Walks the given paths, reading `*.py` and `*.ipynb` (skips `.git`, `.venv`,
  `__pycache__`, `.ipynb_checkpoints`, `node_modules`, `build`, … by default).
- Collects every `import` via the `ast` module, with a regex fallback for files
  that don't parse.
- In notebooks: ignores `%`/`!` magic lines, but picks up `%pip install …` /
  `!pip install …` (and Databricks `# MAGIC %pip install …`) as explicit deps.
- Classifies each top-level module as **stdlib** / **third_party** / **local**
  (a module or package found inside the scanned tree) / **relative**.
- Maps common import names to their PyPI distribution: `cv2` → `opencv-python`,
  `sklearn` → `scikit-learn`, `PIL` → `Pillow`, `delta` → `delta-spark`, …

### Limitations

- stdlib classification is for *this* Python version.
- "local" finds `foo.py` and packages with `__init__.py`; bare-directory
  namespace packages are missed. Scanning a **single file** can't see its
  siblings, so a sibling import shows up as third-party.
- The import→distribution map is best-effort; unmapped names are emitted as-is.

## Use as a library

```python
from pathlib import Path
from imports_scan import scan

result = scan([Path("analysis")])
result.requirements()      # ['pandas', 'requests', 'scikit-learn']
result.third_party         # {'pandas': ['analysis/a.ipynb', ...], ...}
result.stdlib              # {'os': [...], 'json': [...]}
result.local               # {'mymod': [...]}
result.relative            # {'.': [...]}
result.pip_installs        # {'requests': ['analysis/a.ipynb']}
result.errors              # [('bad.ipynb', 'JSONDecodeError: ...')]
```

## Use from the command line

```bash
python imports_scan.py                       # scan the current directory
python imports_scan.py a.py notebooks/       # specific files / dirs
python imports_scan.py . > requirements.txt  # names only, one per line
python imports_scan.py . --json              # full structured report
python imports_scan.py . --all               # also list stdlib / local / relative
python imports_scan.py . --with-files        # annotate each dep with its source files
python imports_scan.py . --exclude scratch   # skip an extra directory name
python imports_scan.py . --strict            # exit 1 if a file couldn't be parsed
python imports_scan.py --selftest
```

Exit code: `0` normally · `1` with `--strict` when a file could not be read/parsed.

## Test

```bash
uv run pytest tools/imports-scan
```
