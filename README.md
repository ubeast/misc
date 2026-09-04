# one-file-tools

A grab-bag of independent single-file tools. Each lives in its own directory
under [`tools/`](tools/) with its own README and tests. Nothing is installable;
copy the one `.py` file wherever you need it.

| tool | what it does | stdlib only |
| --- | --- | --- |
| [`iso6346`](tools/iso6346/) | Validate ISO 6346 shipping-container numbers and calculate the check digit | yes |
| [`ic-filename-parser`](tools/ic-filename-parser/) | Parse EBSO / DLMS / DTEB Interface Change (IC) PDF filenames into a table | yes |
| [`unlocode`](tools/unlocode/) | Validate UN/LOCODE format and split into country + location code | yes |
| [`scac`](tools/scac/) | Validate SCAC (Standard Carrier Alpha Code) format and classify its reserved suffix | yes |
| [`colname-normalize`](tools/colname-normalize/) | Turn messy column headers into safe, unique `snake_case` identifiers | yes |
| [`imports-scan`](tools/imports-scan/) | Scan `.py` / `.ipynb` files for imports and emit a dependency list | yes |
| [`repo-inventory`](tools/repo-inventory/) | List every GitHub / GitLab repo you can see as one Markdown / CSV / JSON table | yes |
| [`nb-extract`](tools/nb-extract/) | Pull selected cells (by language / tag / regex) out of Jupyter or Databricks notebooks | yes |
| [`nb-secrets`](tools/nb-secrets/) | Scan notebooks and scripts (cell source *and* outputs) for hard-coded secrets | yes |
| [`to-markdown`](tools/to-markdown/) | Print a `pandas` DataFrame as a Markdown table | no (`pandas`) |
| [`dbricks-folder-archiver`](tools/dbricks-folder-archiver/) | Databricks notebook: archive the workspace folder it runs from to a new private GitLab project | no (`requests`; Databricks notebook) |

## Tests

`pyproject.toml` exists only to pin the test tooling (`pytest`, plus `requests`
for the one tool whose tests need it) and point pytest at `tools/`. From the
repo root:

```bash
uv run pytest                 # every tool
uv run pytest tools/iso6346   # one tool
```

Each tool's `tests/conftest.py` puts that tool's directory on `sys.path`, so the
tests `import <tool>` by name.
