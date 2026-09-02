# nb-secrets

Scan notebooks and scripts for hard-coded secrets — a cheap pre-commit / CI
check for the token you pasted into a cell and forgot.

One file: [`nb_secrets.py`](nb_secrets.py), standard library only. Copy it
wherever you need it — no install.

```
$ python nb_secrets.py notebooks/
notebooks/etl.ipynb:cell 4:line 2: [databricks-pat] dapi****************************
notebooks/etl.ipynb:cell 9:output: [private-key] -----BEGIN RSA PRIVATE KEY-----
2 finding(s)
```

## What it looks at

- `.py` — line by line.
- `.ipynb` — every cell source **and every text output** (`stream` text,
  `text/plain`, `application/json`). Outputs leak secrets constantly and diffs
  hide them.

## Rules

AWS access key IDs & secret keys · GitHub & GitLab tokens · Slack tokens and
webhooks · Google API keys · Databricks PATs (`dapi…`) · JWTs · PEM private-key
blocks · URLs with an embedded `user:password@` · generic
`password=` / `token=` / `api_key=` assignments to a literal.

Obvious placeholders (`"xxxx"`, `"<your-token>"`, `"${VAR}"`, `"changeme"`, …)
are ignored. `--entropy` additionally flags high-entropy string literals (noisier).

`python nb_secrets.py --list-rules` prints them all.

## Suppressing a match

Put `# nbsecrets: allow` or `# pragma: allowlist secret` on the same line (source
only — a secret in cell **output** is always reported). Exclude whole paths with
`--exclude GLOB` (repeatable, matches the full path or the basename).

## Use as a library

```python
from pathlib import Path
from nb_secrets import scan_path

for f in scan_path(Path("notebooks")):
    print(f.location, f.rule, f.redacted)
    # f.path, f.cell, f.line, f.in_output, f.description
```

`scan_text(text, *, path=..., cell=..., in_output=..., use_entropy=...)` scans a
single string if you already have the content.

## Command line

```bash
python nb_secrets.py                     # scan the current directory
python nb_secrets.py a.ipynb src/        # specific files / dirs
python nb_secrets.py . --json            # findings as JSON
python nb_secrets.py . --entropy         # + high-entropy string literals
python nb_secrets.py . --exclude '*_vendored.py'
python nb_secrets.py --list-rules
python nb_secrets.py --selftest
```

Exit code: `0` clean · `1` at least one finding.

Matches are printed **redacted** (first few characters, rest masked). Use it in
a pre-commit hook or CI step.

## Test

```bash
uv run pytest tools/nb-secrets
```

## Caveat

Regex-based detectors have false positives and false negatives. This is a
speed-bump, not a guarantee — for anything serious pair it with server-side
secret scanning and rotate anything it finds.
