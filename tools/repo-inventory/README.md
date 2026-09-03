# repo-inventory

One flat table of **every repo you can see on GitHub and/or GitLab** — Markdown,
CSV, or JSON. For auditing descriptions, licenses, missing READMEs, stale
projects, and forks you forgot about without clicking through two web UIs.

One file: [`repo_inventory.py`](repo_inventory.py), standard library only
(`urllib`). Copy it wherever you need it — no install.

```
$ export GITHUB_TOKEN=ghp_xxx GITLAB_TOKEN=glpat-xxx
$ python repo_inventory.py --github --gitlab --format md -o repos.md
```

## Columns

`platform`, `owner`, `name`, `description`, `visibility`, `is_fork`, `archived`,
`language`, `stars`, `forks`, `watchers`, `open_issues`, `topics`, `license`,
`has_readme`, `contributors`, `default_branch`, `created`, `updated`, `pushed`,
`url`

In the Markdown / CSV table: booleans render `yes` / `no`, a **`?`** means the
value is unknown (see `--full`), and a blank cell means known-to-be-empty. JSON
keeps real types and uses `null` for unknown.

### `forks` vs `is_fork` — opposite directions

- **`forks`** is `forks_count`: how many *other* people forked **this** repo. It's
  an inbound popularity signal, alongside `stars` and `watchers`. Sort by it
  (`--sort` doesn't cover it, but the CSV/JSON does) to see what's getting picked
  up.
- **`is_fork`** is `true` when **you** created this repo by forking someone
  else's. It says nothing about the repo's popularity — a fork you never touched
  still shows `is_fork: yes` with `forks: 0`.

`--exclude-forks` filters on `is_fork`, i.e. it drops the repos *you* forked, not
the ones others forked from you.

## Targets

| flag | what it lists | token |
| --- | --- | --- |
| `--github` | your repos (owner + collaborator + org member) | **required** (`GITHUB_TOKEN`) |
| `--github USER` | that user's / org's repos | optional (public without) |
| `--gitlab` | every project you're a member of | **required** (`GITLAB_TOKEN`) |
| `--gitlab USER` | that user's projects | optional (public without) |

Pass one or both. For a self-managed GitLab, add
`--gitlab-url https://gitlab.example.com`.

Tokens are read from the environment and never printed. Scopes needed:

- **GitHub** — classic: `repo` for private repos, nothing for public only.
  Fine-grained: read-only *Contents* + *Metadata*.
- **GitLab** — `read_api`.

Override the env var names with `--github-token-env` / `--gitlab-token-env`.

## `--full` (slower)

Without it, every column comes from the one list endpoint — a page per 100
repos, a handful of requests total.

`--full` adds **per repo**:

| column | GitHub | GitLab |
| --- | --- | --- |
| `contributors` | count via paginated `/contributors` (incl. anonymous) | `X-Total` header on `/repository/contributors` |
| `has_readme` | `GET /readme` → 200 / 404 | already free (from `readme_url`) |
| `watchers` | real "watching" count (`subscribers_count`) | — (GitLab has no watch concept) |

> The list endpoint's `watchers_count` on GitHub is a legacy alias for the star
> count, so this column is left blank for GitHub unless you pass `--full`.

On a 200-repo account `--full` turns ~4 requests into ~400 — expect a minute or
two, and possibly a secondary rate-limit (the script sleeps and retries once,
then leaves that repo's extra fields unknown).

## Other options

| flag | effect |
| --- | --- |
| `--format {md,csv,json}` | output format (default `md`) |
| `--sort {name,created,updated,pushed,stars}` | sort order (default `pushed`, newest first) |
| `--exclude-forks` / `--exclude-archived` | drop those rows |
| `-o FILE` | write to a file instead of stdout |
| `-q` | no progress on stderr |
| `--selftest` | run built-in checks and exit |

## Use as a library

```python
import os
from repo_inventory import fetch_github, fetch_gitlab, render

records = fetch_github(token=os.environ["GITHUB_TOKEN"], full=False)
records += fetch_gitlab(token=os.environ["GITLAB_TOKEN"])
print(render(records, fmt="csv"))
```

Every `fetch_*` returns `list[RepoRecord]` (a frozen dataclass with `.as_dict()`
for JSON-native types and `.as_row()` for an all-strings table row). `ApiError`
is raised with a pointed message on auth / rate-limit / not-found failures.

## Tests

```bash
uv run pytest tools/repo-inventory
```

Tests never hit the network — they cover the normalisation, pagination-header
parsing, rendering, and sorting helpers, plus the no-network CLI paths. The live
API calls are exercised by hand; `--selftest` runs the same offline checks.
