# iso6346

Validate ISO 6346 shipping-container numbers and calculate their check digit.

One file: [`iso6346.py`](iso6346.py), standard library only. Copy it wherever
you need it — no install.

```
C S Q U 3 0 5 4 3 8 3
└─┬─┘ │ └──┬───┘ │
  │   │    │     └── check digit  (1 digit)
  │   │    └──────── serial number (6 digits)
  │   └───────────── equipment category identifier (1 letter: U, J or Z)
  └───────────────── owner code (3 letters, registered with the BIC)
```

## Use as a library

```python
from iso6346 import is_valid, check_digit, parse
```

| function | for | behaviour |
| --- | --- | --- |
| `is_valid(number) -> bool` | validating a full number | `True` only if it is 11 characters and the check digit matches. Malformed input or a 10-char number → `False`. **Never raises.** |
| `check_digit(number) -> int` | calculating the check digit | give it the 10-char number (or an 11-char one — the last char is ignored). Returns `0`–`9`. Raises `ValueError` on input that is not shaped like a container number. |
| `parse(number) -> ContainerNumber` | needing the pieces / the reason | frozen dataclass: `owner_code`, `category_identifier`, `serial_number`, `check_digit`, `provided_check_digit`, `is_valid`, `is_complete`, `category_is_standard`, `normalized`. Raises `ValueError` on malformed input. |

All three accept any case and ignore spaces and hyphens.

```python
is_valid("CSQU3054383")       # True
is_valid("CSQU3054384")       # False  (bad check digit)
is_valid("garbage")           # False  (no exception)

check_digit("CSQU305438")     # 3
check_digit("mscu 123456")    # 5

c = parse("CSQU 305438-3")
c.owner_code, c.serial_number, c.is_valid   # ('CSQ', '305438', True)
```

### Notes

- **Remainder 10 → check digit 0.** A weighted sum whose remainder mod 11 is 10
  yields check digit `0` (ISO 6346, Annex A). Serial numbers that land there are
  ambiguous with genuine 0-check-digit numbers and are discouraged by the BIC,
  but `0` is the value the standard defines.
- **Category identifier.** ISO 6346 defines only `U`, `J`, `Z`. A non-standard
  identifier (e.g. an AAR rail code) can still have a matching check digit;
  `parse(...).category_is_standard` reports it separately from validity.

## Use from the command line

```bash
python iso6346.py CSQU3054383            # validate one or more numbers
python iso6346.py "csqu 305438" --json   # machine-readable, one object per line
python iso6346.py --ref                  # print the letter → value table
python iso6346.py --selftest             # run the built-in doctests + assertions
```

Exit code: `0` all valid · `1` a check digit did not match · `2` unparseable input.

## Test

```bash
uv run pytest tools/iso6346        # or: pytest tools/iso6346
```

`tests/test_iso6346.py` includes a 20,000-case cross-check of the check-digit
algorithm against an independent reimplementation written from the ISO 6346
description, so a typo in the production weights or letter table would surface.

## Sources

- <https://en.wikipedia.org/wiki/ISO_6346>
- <https://www.bic-code.org/check-digit-calculator/>
