# ic-filename-parser

Parse EBSO / DLMS / DTEB **Interface Change (IC)** PDF filenames into a table.
Rewrite of a one-file script; same parsing intent, with the date bug and the
DTEB crash fixed.

It is still one file: [`scripts/ic_filename_parser.py`](scripts/ic_filename_parser.py),
standard library only (no pandas). Copy it wherever you need it — no install.

## Use

```bash
# CSV to stdout for the PDFs in ./downloads
python scripts/ic_filename_parser.py ./downloads

# to a file, and also onto the clipboard
python scripts/ic_filename_parser.py ./downloads -o ics.csv --clipboard
```

`--clipboard` shells out to `pbcopy` (macOS) / `clip` (Windows) /
`xclip` or `xsel` (Linux), and warns to stderr if none is found.

As a library (put the file on `sys.path`, then import it by name):

```python
from pathlib import Path
from ic_filename_parser import parse_filename, scan_directory, rows_to_csv

rec = parse_filename("004010M511_3_MA05_20220803_ADC_1234.pdf")
rows = scan_directory(Path("./downloads"))   # list[ICRecord]
print(rows_to_csv(rows))
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
  *the 20th of August* and made the `MonthYYYY` branch dead code. Date shapes
  are now tried most-specific-first; see the `parse_ebso_date` comments for the
  full order and the plausible-year window (`2000`–`2099`, applied to every
  4-digit-year shape).
- **DTEB filenames without a release no longer crash** (`None.upper()` →
  `AttributeError`), e.g. `41D856.pdf`.
- **Unknown track codes report `"Unknown"`**, not `"Functional Acknowledgement"`.
- **`_ADC_<n>` with no preceding publication date** keeps its reference instead
  of the date group swallowing `ADC`.
- **Empty directory still yields the full column header** (`rows_to_csv([])`),
  not a headerless output.
- No import-time side effects; `pathlib` throughout; type hints;
  case-insensitive `*.pdf` / `*.PDF` matching.

## Known limitation

The DLMS pattern's transaction-set segment is exactly three digits. Filenames
where the generation digit is glued to the transaction set (`...M0511...`) can
mis-split, or fail the pattern entirely. Real examples plus a fixture test are
the right way to tighten the pattern — don't widen `\d{3}` blindly. Such a
filename still shows up: an IC-shaped name (`<4-6 digits><letter><3 digits>…`)
that no pattern matches gets its full base echoed into `Unparsed_Trailing`
instead of being filed silently as "Non-Standard EDI Convention".

## Test

The suite (`tests/`) runs against the script via `tests/conftest.py`, which
puts `scripts/` on the path.

```bash
uv run pytest        # or: pip install pytest && pytest
```
