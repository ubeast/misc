# ic-filename-parser

Parse EBSO / DLMS / DTEB **Interface Change (IC)** PDF filenames into a structured
table. Rewrite of a one-file script; same parsing intent, with the date bug and
the DTEB crash fixed.

## Install

```bash
uv sync --extra dev      # dev deps (pytest)
# or, just the runtime:
uv pip install -e .
```

## Use

```bash
# CSV to stdout for the PDFs in ./downloads
uv run ic-filename-parser ./downloads

# to a file, and also onto the clipboard
uv run ic-filename-parser ./downloads -o ics.csv --clipboard
```

Library:

```python
from ic_filename_parser import parse_filename
from ic_filename_parser.cli import scan_directory

rec = parse_filename("004010M511_3_MA05_20220803_ADC_1234.pdf")
df = scan_directory(Path("./downloads"))
```

## Output columns

Same as the original script plus three new columns:

| column | meaning |
| --- | --- |
| `Publication_Date_Precision` | `day`, `month` (day-of-month defaulted to the 1st), or `""` |
| `Track_Inferred` | `True` when `Track` was guessed (997 → `F`, everything else → `S`) rather than read from the filename |
| `Unparsed_Trailing` | filename text the pattern could not consume — non-empty means "look at this one" |

## What changed vs. the original script

- **`Aug2024`-style dates fixed.** The old greedy day pattern read `Aug2024` as
  *the 20th of August* and made the `MonthYYYY` branch dead code. Patterns are
  now tried most-specific-first; see `dates.py` for the full order and the
  plausible-year window (`2000`–`2099`).
- **DTEB filenames without a release no longer crash** (`None.upper()` →
  `AttributeError`), e.g. `41D856.pdf`.
- **Unknown track codes report `"Unknown"`**, not `"Functional Acknowledgement"`.
- **Empty directory yields a DataFrame with the full column set**, not a
  columnless frame.
- No import-time side effects; `pathlib` throughout; type hints; `src/` layout;
  case-insensitive `*.pdf` / `*.PDF` matching.

## Known limitation

The DLMS pattern's transaction-set segment is exactly three digits. Filenames
where the generation digit is glued to the transaction set (`...M0511...`) can
mis-split. Real examples plus a fixture test are the right way to tighten the
pattern — don't widen `\d{3}` blindly. Anything unconsumed lands in
`Unparsed_Trailing`.

## Test

```bash
uv run pytest
```
