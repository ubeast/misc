# scac

Validate the shape of a **SCAC** (Standard Carrier Alpha Code) and report the
meaning of its reserved final letter.

One file: [`scac.py`](scac.py), standard library only. Copy it wherever you need
it — no install.

```
M A E U
└─┬─┘ └── reserved suffix: U = ocean / container carrier
  └────── base code
```

## Scope

A SCAC is 2–4 letters identifying a carrier, issued by the
[NMFTA](https://nmfta.org/scac/). This tool validates the **format** and
classifies the reserved final letter. It does **not** know which codes have
actually been issued — for that you need the NMFTA's paid directory.

| final letter | convention |
| --- | --- |
| `…U` | freight containers / international ocean carriers |
| `…X` | railroads and railroad-affiliated companies |
| `…Z` | trucking companies |

The suffix counts only when ≥ 2 letters remain in front of it (`MAEU` → `MAE`+`U`;
`FX` stays `FX`). The convention is widely followed but not strictly enforced,
so `parse(...).suffix` is reported independently of validity.

## Use as a library

```python
from scac import is_valid, parse

is_valid("MAEU")        # True
is_valid("maeu")        # True   (case-insensitive, ignores spaces/hyphens)
is_valid("TOOLONG")     # False  (never raises)

s = parse("SCAX")               # raises ValueError on a bad shape
s.code, s.base, s.suffix        # ('SCAX', 'SCA', 'X')
s.suffix_meaning                # 'railroads and railroad-affiliated companies'
s.length                        # 4
```

`parse` returns a frozen `Scac` dataclass: `code`, `base`, `suffix`,
`suffix_meaning`, `length`, `is_valid`.

## Use from the command line

```bash
python scac.py MAEU CSXT              # validate / classify one or more codes
python scac.py "hl-xu" --json         # one JSON object per line
python scac.py --suffixes             # print the reserved-letter table
python scac.py --selftest
```

Exit code: `0` all well-formed · `2` a code was not 2–4 letters.

## Test

```bash
uv run pytest tools/scac
```

## Sources

- <https://en.wikipedia.org/wiki/Standard_Carrier_Alpha_Code>
- <https://nmfta.org/scac/>
