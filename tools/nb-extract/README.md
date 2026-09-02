# nb-extract

Pull selected cells out of a Jupyter (`.ipynb`) or Databricks source-export
notebook — by language, tag, position, or a regex on the source.

One file: [`nb_extract.py`](nb_extract.py), standard library only. Copy it
wherever you need it — no install.

```bash
python nb_extract.py etl.ipynb --lang sql --out etl.sql       # lint the SQL elsewhere
python nb_extract.py job.py --lang python --grep "spark.read" # find the reads
python nb_extract.py analysis.ipynb --format json             # machine-readable cells
python nb_extract.py etl.ipynb --split cells/                 # one file per cell
```

## Inputs

| format | how cells are found |
| --- | --- |
| Jupyter `.ipynb` | kernel language, overridden by a leading `%%sql` / `%%bash` cell magic or a `%sql` / `%md` line magic (Databricks-on-Jupyter style) |
| Databricks export (`.py`, `.sql`, `.scala`, `.r`) | split on `# COMMAND ----------`; `# MAGIC %sql` blocks decoded back to their real language and source; `# DBTITLE` captured as the cell title |

The format is auto-detected; force it with `--format-hint`. Languages are
normalized to `python`, `sql`, `markdown`, `shell`, `r`, `scala`, `raw`.

## Use as a library

```python
from pathlib import Path
from nb_extract import parse_notebook, select, render

cells = parse_notebook(Path("etl.ipynb"))       # list[Cell]
sql = select(cells, languages=["sql"], grep="JOIN")
print(render(sql))                               # concatenated, with `-- cell N` markers
```

`Cell`: `index`, `language`, `source`, `cell_type`, `tags`, `title`.

`select(cells, *, languages=None, tags=None, grep=None, indices=None,
include_markdown=False)` — selectors of different kinds are AND-ed, values
within one kind OR-ed. With no selectors, code cells are returned.

## Command line

```
--lang LANG          keep cells of this language (repeatable)
--tag TAG            keep cells with this tag (repeatable)
--grep REGEX         keep cells whose source matches REGEX
--index SPEC         keep cells by position, e.g. 0,2,5-7
--include-markdown   include markdown/raw when no other selector is given
--format {text,json,count}
--no-separators      omit the `# cell N` markers in text output
--out FILE           write text output to FILE
--split DIR          one file per selected cell (NNN_<lang>.<ext>)
--by-language DIR     one concatenated file per language
--format-hint {ipynb,databricks}
--selftest
```

Exit code: `0` ok · `2` file missing or unparseable.

## Test

```bash
uv run pytest tools/nb-extract
```
