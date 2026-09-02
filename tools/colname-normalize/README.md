# colname-normalize

Turn messy column headers into safe, unique, lower_snake_case identifiers.

One file: [`colname_normalize.py`](colname_normalize.py), standard library only.
Copy it wherever you need it — no install.

```
"Unit Price ($)"   ->  unit_price_usd
"customerID"       ->  customer_id
"Ship-To  Country" ->  ship_to_country
"2020 Revenue"     ->  n2020_revenue
"from"             ->  from_
["Total", "total"] ->  ["total", "total_2"]
```

## What it does

`normalize(name)` applies, in order:

1. Unicode NFKD + drop accents (`"Ünit"` → `"Unit"`), ASCII-only by default.
2. Symbols → words (optional): `% & # @ $ + ° € £` become `pct and num at usd plus deg eur gbp`.
3. Split camelCase / PascalCase / acronym runs (`"HTTPServerError"` → `"HTTP_Server_Error"`).
4. Every run of non-alphanumerics → a single `_`; collapse, trim, lower-case.
5. Empty result → `"column"` (configurable).
6. Leading digit → prefixed with `n` (configurable; `--digit-prefix ""` keeps the digit).
7. Python keyword → trailing `_`; with `avoid_sql_keywords`, common SQL reserved words too.
8. `max_length` (optional) truncates.

`normalize` is idempotent. `normalize_all` also makes the whole list unique by
appending `_2`, `_3`, …

## Use as a library

```python
from colname_normalize import normalize, normalize_all, build_mapping, Options

normalize("Unit Price ($)")                  # "unit_price_usd"
normalize_all(["Total", "total", "TOTAL"])    # ["total", "total_2", "total_3"]
build_mapping(["A B", "A-B"])                 # {"A B": "a_b", "A-B": "a_b_2"}

# rename a DataFrame's columns
df.columns = normalize_all(list(df.columns))
# or in Spark
for old, new in build_mapping(df.columns).items():
    df = df.withColumnRenamed(old, new)
```

| function | returns | notes |
| --- | --- | --- |
| `normalize(name, options=Options())` | `str` | one name; idempotent; never raises |
| `normalize_all(names, options=Options())` | `list[str]` | same length/order as input, de-duplicated |
| `build_mapping(names, options=Options())` | `dict[str, str]` | `{original: normalized}`; last wins on duplicate originals |

`Options(...)` fields: `fallback`, `digit_prefix`, `replace_symbols`,
`symbol_words`, `split_camel_case`, `lower`, `ascii_only`,
`avoid_python_keywords`, `avoid_sql_keywords`, `max_length`.

## Use from the command line

```bash
python colname_normalize.py "Unit Price ($)" "customerID"   # names as args
python colname_normalize.py --csv data.csv                  # normalize a header row
python colname_normalize.py --csv - < data.csv              # ... from stdin
python colname_normalize.py --stdin < names.txt             # one name per line
python colname_normalize.py "A B" "A-B" --json              # [{original, normalized}, ...]
python colname_normalize.py --csv data.csv --check          # CI: exit 1 if not already clean
python colname_normalize.py --selftest
```

Flags: `--names-only`, `--no-symbols`, `--no-split-camel`, `--keep-case`,
`--sql-reserved`, `--max-length N`, `--digit-prefix STR`.

Exit code: `0` ok · `1` `--check` found names that would change · `2` bad usage.

## Test

```bash
uv run pytest tools/colname-normalize
```
