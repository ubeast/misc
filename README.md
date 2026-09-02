# misc

A grab-bag of independent single-file tools. Each lives in its own directory
under [`tools/`](tools/) with its own README and tests. Nothing is installable;
copy the one `.py` file wherever you need it.

| tool | what it does | stdlib only |
| --- | --- | --- |
| [`tools/iso6346`](tools/iso6346/) | Validate ISO 6346 shipping-container numbers and calculate the check digit | yes |
| [`tools/ic-filename-parser`](tools/ic-filename-parser/) | Parse EBSO / DLMS / DTEB Interface Change (IC) PDF filenames into a table | yes |
| [`tools/to-markdown`](tools/to-markdown/) | Print a `pandas` DataFrame as a Markdown table | no (`pandas`) |

## Tests

`pyproject.toml` exists only to pin `pytest` and point it at `tools/`. From the
repo root:

```bash
uv run pytest                 # every tool
uv run pytest tools/iso6346   # one tool
```

Each tool's `tests/conftest.py` puts that tool's directory on `sys.path`, so the
tests `import <tool>` by name.
