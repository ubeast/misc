# one-file-tools

A grab-bag of independent, copy-one-file utilities. See `README.md` for the tool index.

## What this repo is

Every tool is **one `.py` file** that runs as a CLI and imports as a module. Nothing
is packaged or installable — the deliverable is a file you paste where you need it
(often a locked-down work machine or a Databricks notebook). Almost all are
**standard-library only**; the few exceptions are marked in the README table.

## Layout

```
tools/<name>/
  <name>.py            # the tool — snake_case module name matching the dir
  README.md            # what it does, scope, library-use examples
  tests/
    conftest.py        # puts tools/<name>/ on sys.path so tests `import <name>`
    test_<name>.py
```

`pyproject.toml` exists **only** to pin the test tooling and point pytest at `tools/`.
It is `package = false` — do not add runtime dependencies to it. If a tool needs a
third-party package, note it in the README table and (only if its tests need it) add
it to the `dev` dependency group.

## Testing

```bash
uv run pytest                 # everything
uv run pytest tools/<name>    # one tool
```

Use `uv`, not pip/poetry. Many tools also carry a `--selftest` flag that runs their
own assertions and doctests.

## Conventions

- Type hints everywhere; `from __future__ import annotations`; `pathlib` over `os.path`.
- Keep the heavy module docstring / `__all__` / `main(argv) -> int` shape the existing
  tools use — match the file you're working near.
- Never hardcode values without explaining why in a comment.
- Write for a follow-on developer with no context.
- Flag any speed/performance tradeoff explicitly (in code comments and the README).

## Workflow

- One PR per change; branch off `main`. The owner merges.
- Non-trivial work gets a GitHub issue first (`gh issue develop` to branch from it).
  Typo-tier fixes can skip the issue.
- The repo has a curated label set — apply `bug` / `enhancement` / `documentation` /
  `question` / `research` / `blocked` / `needs-decision` etc. rather than inventing new ones.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

## Adding a tool

1. `tools/<name>/<name>.py` — single file, stdlib-only if at all possible.
2. `tools/<name>/README.md` and `tools/<name>/tests/` (copy `conftest.py` from a
   sibling and change the name).
3. Add a row to the table in the top-level `README.md`.
4. `uv run pytest tools/<name>` green, then open the PR.
