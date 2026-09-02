# unlocode

Validate the format of a **UN/LOCODE** and split it into country + location.

One file: [`unlocode.py`](unlocode.py), standard library only. Copy it wherever
you need it — no install.

```
U S   N Y C
└┬┘   └─┬─┘
 │      └── location code: 3 chars, letters A-Z or digits 2-9 (no 0/1)
 └───────── country code:  2 letters, ISO 3166-1 alpha-2
```

## Scope

This checks the **shape** and validates the **country code** against the bundled
ISO 3166-1 alpha-2 list (plus `XZ` for international waters). It does **not**
know whether a location code has actually been assigned — the UN/LOCODE database
has ~100,000 entries and is republished twice a year, too large to embed. So a
well-formed code with a real country returns `is_valid` **True** even if UNECE
never issued it.

## Use as a library

```python
from unlocode import is_valid, parse

is_valid("USNYC")       # True
is_valid("US NYC")      # True   (space/hyphen between halves tolerated, any case)
is_valid("USNY1")       # False  (digit 1 not allowed; never raises)
is_valid("ZZNYC")       # False  (unknown country)

loc = parse("de ham")           # raises ValueError on a bad shape
loc.country, loc.location       # ('DE', 'HAM')
loc.normalized, loc.display     # ('DEHAM', 'DE HAM')
loc.country_is_known            # True
loc.is_valid                    # True
```

`parse` returns a frozen `UnLocode` dataclass: `country`, `location`,
`normalized`, `display`, `country_is_known`, `country_note`,
`location_is_wellformed`, `is_valid`.

## Use from the command line

```bash
python unlocode.py USNYC DEHAM          # validate / split one or more codes
python unlocode.py "de ham" --json      # one JSON object per line
python unlocode.py --countries          # print the accepted country codes
python unlocode.py --selftest
```

Exit code: `0` all valid · `1` well-formed but unknown country · `2` unparseable.

## Test

```bash
uv run pytest tools/unlocode
```

## Sources

- <https://unece.org/trade/uncefact/unlocode>
- <https://en.wikipedia.org/wiki/UN/LOCODE>
