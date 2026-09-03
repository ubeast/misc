#!/usr/bin/env python3
"""Inventory every repository you can see on GitHub and/or GitLab into one table.

Single file, standard library only (``urllib``). Usable as a CLI or importable
as a module.

The point: get one flat table -- Markdown, CSV, or JSON -- of all your repos
across both hosts, so you can audit descriptions, licenses, missing READMEs,
stale projects, forks you forgot about, etc. without clicking through two web
UIs.

    $ export GITHUB_TOKEN=ghp_xxx GITLAB_TOKEN=glpat-xxx
    $ python repo_inventory.py --github --gitlab --format md -o repos.md

What you get per repo
--------------------
platform, owner, name, description, visibility, is_fork, archived, language,
stars, forks, watchers, open_issues, topics, license, has_readme, contributors,
default_branch, created, updated, pushed, url

Some of those columns are only filled in with ``--full`` (see below).

Targets
-------
``--github [USER]``
    No value  -> your repos as the authenticated token owner (owner + collaborator
                 + org member). Requires ``GITHUB_TOKEN``.
    With USER -> that user's/org's public repos (plus private ones the token can
                 see). Works tokenless for public repos, but you'll hit the
                 60 req/hour unauthenticated rate limit fast.

``--gitlab [USER]``
    No value  -> every project you're a member of. Requires ``GITLAB_TOKEN``.
    With USER -> that user's public projects.
    Use ``--gitlab-url https://gitlab.example.com`` for self-managed instances.

At least one of ``--github`` / ``--gitlab`` is required.

Tokens
------
Read from the environment: ``GITHUB_TOKEN``, ``GITLAB_TOKEN`` (``CI_JOB_TOKEN``
is *not* used -- it can't list projects). Override the env var names with
``--github-token-env`` / ``--gitlab-token-env`` if yours are called something
else. Tokens are never printed.

    GitHub token scope:  classic -> `repo` (private) or none (public only);
                         fine-grained -> read-only "Contents" + "Metadata".
    GitLab token scope:  `read_api`.

``--full`` (slower: extra API calls per repo)
--------------------------------------------
Without it, every column comes from the single list endpoint -- one page of
results per 100 repos, fast. ``--full`` adds, per repo:

  * contributors  -- a count (GitHub: paginated ``/contributors`` incl. anon;
                     GitLab: the ``X-Total`` header). N extra requests.
  * has_readme    -- GitHub only needs a call here (``/readme`` -> 200/404);
                     GitLab always reports it for free from ``readme_url``.
  * watchers      -- GitHub's list endpoint reports ``watchers_count`` but it is
                     a legacy alias for the star count and is useless, so without
                     ``--full`` this column is left blank for GitHub. ``--full``
                     fetches the real "watching" number (``subscribers_count``).
                     GitLab has no watch concept; always blank there.

So on a 200-repo account, ``--full`` turns ~4 requests into ~400. Expect it to
take a minute or two and possibly bump into secondary rate limits (the script
sleeps and retries once on a 429 / 403-rate-limit, then gives up on that repo).

For developers
--------------
    from repo_inventory import fetch_github, fetch_gitlab, render, RepoRecord

    records = fetch_github(token=os.environ["GITHUB_TOKEN"])
    print(render(records, fmt="csv"))

Every ``fetch_*`` returns ``list[RepoRecord]``; ``RepoRecord`` is a frozen
dataclass with ``.as_row()`` (dict of str for tabular output) and ``.as_dict()``
(native types for JSON).

Run the built-in checks with:  python3 repo_inventory.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterator

__all__ = [
    "RepoRecord",
    "COLUMNS",
    "fetch_github",
    "fetch_gitlab",
    "github_repo_to_record",
    "gitlab_repo_to_record",
    "render",
    "parse_link_header",
    "last_page_from_link",
]

GITHUB_API = "https://api.github.com"
GITLAB_DEFAULT_URL = "https://gitlab.com"
USER_AGENT = "repo-inventory/1.0 (+https://github.com/ubeast/one-file-tools)"

# Column order for the Markdown / CSV table. JSON keeps the dataclass field
# order (same list). Keep this in sync with RepoRecord's fields.
COLUMNS: tuple[str, ...] = (
    "platform",
    "owner",
    "name",
    "description",
    "visibility",
    "is_fork",
    "archived",
    "language",
    "stars",
    "forks",
    "watchers",
    "open_issues",
    "topics",
    "license",
    "has_readme",
    "contributors",
    "default_branch",
    "created",
    "updated",
    "pushed",
    "url",
)


@dataclass(frozen=True)
class RepoRecord:
    """One repository, normalised across hosts.

    ``None`` means "not known" (e.g. ``contributors`` without ``--full``, or
    ``watchers`` for GitLab). Empty string means "known to be empty" (e.g. no
    description). This distinction survives into JSON output; in the Markdown /
    CSV table ``None`` renders as ``?`` and empty string as blank.
    """

    platform: str
    owner: str
    name: str
    description: str
    visibility: str
    is_fork: bool
    archived: bool
    language: str
    stars: int
    forks: int
    watchers: int | None
    open_issues: int
    topics: list[str] = field(default_factory=list)
    license: str = ""
    has_readme: bool | None = None
    contributors: int | None = None
    default_branch: str = ""
    created: str = ""
    updated: str = ""
    pushed: str = ""
    url: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Native-typed dict, for JSON output."""
        return asdict(self)

    def as_row(self) -> dict[str, str]:
        """All-strings dict, for Markdown / CSV output."""
        row: dict[str, str] = {}
        for col in COLUMNS:
            row[col] = _cell(getattr(self, col))
        return row


def _cell(value: Any) -> str:
    """Render one field value as a table cell."""
    if value is None:
        return "?"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return str(value)


def _date(value: Any) -> str:
    """Truncate an ISO 8601 timestamp to its ``YYYY-MM-DD`` date part."""
    if not value or not isinstance(value, str):
        return ""
    return value[:10] if len(value) >= 10 and value[4] == "-" else value


# --------------------------------------------------------------------------- #
# HTTP plumbing
# --------------------------------------------------------------------------- #


class ApiError(RuntimeError):
    """A non-retryable API failure with a human-readable message."""


def _request(
    url: str,
    headers: dict[str, str],
    *,
    timeout: float = 30.0,
) -> tuple[int, dict[str, str], bytes]:
    """GET ``url``; return ``(status, response_headers_lowercased, body_bytes)``.

    HTTP error responses (4xx/5xx) are returned like any other -- the caller
    decides what a 404 means. Network-level failures raise ``ApiError``.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, hdrs, body
    except urllib.error.HTTPError as exc:
        body = exc.read()
        hdrs = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return exc.code, hdrs, body
    except urllib.error.URLError as exc:
        raise ApiError(f"network error requesting {url}: {exc.reason}") from exc


def _rate_limit_pause(hdrs: dict[str, str]) -> float:
    """Seconds to wait before retrying, from rate-limit headers (capped)."""
    retry_after = hdrs.get("retry-after")
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), 60.0)
    reset = hdrs.get("x-ratelimit-reset")
    if reset and reset.isdigit():
        return min(max(float(reset) - time.time(), 1.0), 60.0)
    return 5.0


def _get_json(
    url: str,
    headers: dict[str, str],
    *,
    retries: int = 1,
) -> tuple[Any, dict[str, str]]:
    """GET a JSON endpoint, retrying once on a rate-limit response.

    Returns ``(parsed_json, response_headers)``. Raises ``ApiError`` with a
    pointed message for auth / rate-limit / other failures.
    """
    attempt = 0
    while True:
        status, hdrs, body = _request(url, headers)
        if status == 200:
            return json.loads(body or b"null"), hdrs
        if status == 204:
            return None, hdrs
        if status in (403, 429) and _is_rate_limited(status, hdrs, body):
            if attempt < retries:
                wait = _rate_limit_pause(hdrs)
                print(
                    f"  rate-limited, sleeping {wait:.0f}s then retrying...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                attempt += 1
                continue
            raise ApiError(
                "rate limit exceeded and retry did not help -- wait a while or "
                "use a token with a higher limit"
            )
        if status in (401, 403):
            raise ApiError(
                f"{status} from {url} -- check the token value and its scopes "
                f"({_short_body(body)})"
            )
        if status == 404:
            raise ApiError(
                f"404 from {url} -- user/group not found, or the token can't see it"
            )
        raise ApiError(f"{status} from {url}: {_short_body(body)}")


def _is_rate_limited(status: int, hdrs: dict[str, str], body: bytes) -> bool:
    if status == 429:
        return True
    if hdrs.get("x-ratelimit-remaining") == "0":
        return True
    return b"rate limit" in body.lower() or b"secondary rate" in body.lower()


def _short_body(body: bytes) -> str:
    text = body.decode("utf-8", "replace").strip().replace("\n", " ")
    return (text[:200] + "...") if len(text) > 200 else text


def parse_link_header(value: str | None) -> dict[str, str]:
    """Parse an RFC 5988 ``Link`` header into ``{rel: url}``.

    >>> parse_link_header('<https://x/p?page=2>; rel="next", <https://x/p?page=9>; rel="last"')
    {'next': 'https://x/p?page=2', 'last': 'https://x/p?page=9'}
    >>> parse_link_header(None)
    {}
    """
    out: dict[str, str] = {}
    if not value:
        return out
    for part in value.split(","):
        segs = part.split(";")
        if len(segs) < 2:
            continue
        link = segs[0].strip().lstrip("<").rstrip(">")
        for attr in segs[1:]:
            attr = attr.strip()
            if attr.startswith("rel="):
                rel = attr[4:].strip().strip('"')
                out[rel] = link
    return out


def last_page_from_link(value: str | None) -> int | None:
    """Return the ``page=`` number of the ``rel="last"`` link, or ``None``.

    >>> last_page_from_link('<https://x?page=1&per_page=1>; rel="last"')
    1
    >>> last_page_from_link(None) is None
    True
    """
    links = parse_link_header(value)
    last = links.get("last")
    if not last:
        return None
    query = urllib.parse.urlparse(last).query
    pages = urllib.parse.parse_qs(query).get("page")
    if pages and pages[0].isdigit():
        return int(pages[0])
    return None


def _paginate_github(url: str, headers: dict[str, str]) -> Iterator[Any]:
    """Yield each item across all pages of a GitHub list endpoint."""
    while url:
        data, hdrs = _get_json(url, headers)
        if isinstance(data, list):
            yield from data
        elif data is not None:
            yield data
        url = parse_link_header(hdrs.get("link")).get("next", "")


def _paginate_gitlab(url: str, headers: dict[str, str]) -> Iterator[Any]:
    """Yield each item across all pages of a GitLab list endpoint."""
    page = 1
    while page:
        sep = "&" if "?" in url else "?"
        data, hdrs = _get_json(f"{url}{sep}per_page=100&page={page}", headers)
        if isinstance(data, list):
            yield from data
        elif data is not None:
            yield data
        nxt = hdrs.get("x-next-page", "").strip()
        page = int(nxt) if nxt.isdigit() and nxt != "" else 0


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #


def _github_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_repo_to_record(repo: dict[str, Any]) -> RepoRecord:
    """Normalise one GitHub repo object into a ``RepoRecord``."""
    lic = repo.get("license") or {}
    return RepoRecord(
        platform="github",
        owner=(repo.get("owner") or {}).get("login", ""),
        name=repo.get("name", ""),
        description=repo.get("description") or "",
        visibility=repo.get("visibility") or ("private" if repo.get("private") else "public"),
        is_fork=bool(repo.get("fork")),
        archived=bool(repo.get("archived")),
        language=repo.get("language") or "",
        stars=int(repo.get("stargazers_count") or 0),
        forks=int(repo.get("forks_count") or 0),
        watchers=None,  # list endpoint's watchers_count is a stars alias; see --full
        open_issues=int(repo.get("open_issues_count") or 0),
        topics=list(repo.get("topics") or []),
        license=lic.get("spdx_id") or lic.get("name") or "",
        has_readme=None,
        contributors=None,
        default_branch=repo.get("default_branch") or "",
        created=_date(repo.get("created_at")),
        updated=_date(repo.get("updated_at")),
        pushed=_date(repo.get("pushed_at")),
        url=repo.get("html_url") or "",
    )


def _github_list_url(user: str | None) -> str:
    if user:
        return f"{GITHUB_API}/users/{urllib.parse.quote(user)}/repos?per_page=100&sort=pushed"
    return (
        f"{GITHUB_API}/user/repos?per_page=100&sort=pushed"
        "&affiliation=owner,collaborator,organization_member"
    )


def _github_contributor_count(full_name: str, headers: dict[str, str]) -> int | None:
    url = f"{GITHUB_API}/repos/{full_name}/contributors?per_page=1&anon=1"
    try:
        data, hdrs = _get_json(url, headers)
    except ApiError:
        return None
    last = last_page_from_link(hdrs.get("link"))
    if last is not None:
        return last
    return len(data) if isinstance(data, list) else 0


def _github_extra(full_name: str, headers: dict[str, str]) -> dict[str, Any]:
    """The per-repo ``--full`` fields for GitHub: watchers + has_readme."""
    out: dict[str, Any] = {}
    try:
        detail, _ = _get_json(f"{GITHUB_API}/repos/{full_name}", headers)
        if isinstance(detail, dict):
            out["watchers"] = detail.get("subscribers_count")
    except ApiError:
        pass
    status, _, _ = _request(f"{GITHUB_API}/repos/{full_name}/readme", headers)
    if status == 200:
        out["has_readme"] = True
    elif status == 404:
        out["has_readme"] = False
    return out


def fetch_github(
    *,
    token: str | None = None,
    user: str | None = None,
    full: bool = False,
    progress: Callable[[str], None] | None = None,
) -> list[RepoRecord]:
    """Fetch all visible GitHub repos as ``RepoRecord``s.

    ``user`` None -> the token owner's repos (token required).
    ``full`` -> add contributors / watchers / has_readme via per-repo calls.
    """
    headers = _github_headers(token)
    if not token and not user:
        raise ApiError("--github with no user needs GITHUB_TOKEN set")
    say = progress or (lambda _msg: None)

    records: list[RepoRecord] = []
    for raw in _paginate_github(_github_list_url(user), headers):
        rec = github_repo_to_record(raw)
        if full:
            full_name = raw.get("full_name") or f"{rec.owner}/{rec.name}"
            say(f"  github: {full_name} (contributors, readme, watchers)")
            patch: dict[str, Any] = {
                "contributors": _github_contributor_count(full_name, headers)
            }
            patch.update(_github_extra(full_name, headers))
            rec = _replace(rec, patch)
        records.append(rec)
    return records


# --------------------------------------------------------------------------- #
# GitLab
# --------------------------------------------------------------------------- #


def _gitlab_headers(token: str | None) -> dict[str, str]:
    return {"PRIVATE-TOKEN": token} if token else {}


def gitlab_repo_to_record(proj: dict[str, Any]) -> RepoRecord:
    """Normalise one GitLab project object into a ``RepoRecord``."""
    namespace = proj.get("namespace") or {}
    lic = proj.get("license") or {}
    topics = proj.get("topics")
    if topics is None:
        topics = proj.get("tag_list") or []
    return RepoRecord(
        platform="gitlab",
        owner=namespace.get("full_path") or namespace.get("path") or "",
        name=proj.get("path") or proj.get("name") or "",
        description=proj.get("description") or "",
        visibility=proj.get("visibility") or "",
        is_fork=bool(proj.get("forked_from_project")),
        archived=bool(proj.get("archived")),
        language="",  # not in the project object; would need an extra languages call
        stars=int(proj.get("star_count") or 0),
        forks=int(proj.get("forks_count") or 0),
        watchers=None,  # GitLab has no watch concept
        open_issues=int(proj.get("open_issues_count") or 0),
        topics=list(topics),
        license=lic.get("nickname") or lic.get("name") or lic.get("key") or "",
        has_readme=bool(proj.get("readme_url")) if "readme_url" in proj else None,
        contributors=None,
        default_branch=proj.get("default_branch") or "",
        created=_date(proj.get("created_at")),
        updated=_date(proj.get("last_activity_at")),
        pushed=_date(proj.get("last_activity_at")),
        url=proj.get("web_url") or "",
    )


def _gitlab_list_url(base: str, user: str | None) -> str:
    api = f"{base.rstrip('/')}/api/v4"
    common = "license=true&statistics=false&order_by=last_activity_at"
    if user:
        return f"{api}/users/{urllib.parse.quote(user)}/projects?{common}"
    return f"{api}/projects?membership=true&{common}"


def _gitlab_contributor_count(base: str, pid: int, headers: dict[str, str]) -> int | None:
    api = f"{base.rstrip('/')}/api/v4"
    url = f"{api}/projects/{pid}/repository/contributors?per_page=1"
    try:
        _, hdrs = _get_json(url, headers)
    except ApiError:
        return None
    total = hdrs.get("x-total")
    return int(total) if total and total.isdigit() else None


def fetch_gitlab(
    *,
    token: str | None = None,
    user: str | None = None,
    base_url: str = GITLAB_DEFAULT_URL,
    full: bool = False,
    progress: Callable[[str], None] | None = None,
) -> list[RepoRecord]:
    """Fetch all visible GitLab projects as ``RepoRecord``s.

    ``user`` None -> every project you're a member of (token required).
    ``full`` -> add a contributor count via a per-project call.
    """
    headers = _gitlab_headers(token)
    if not token and not user:
        raise ApiError("--gitlab with no user needs GITLAB_TOKEN set")
    say = progress or (lambda _msg: None)

    records: list[RepoRecord] = []
    for raw in _paginate_gitlab(_gitlab_list_url(base_url, user), headers):
        rec = gitlab_repo_to_record(raw)
        if full and raw.get("id") is not None:
            say(f"  gitlab: {rec.owner}/{rec.name} (contributors)")
            rec = _replace(
                rec,
                {"contributors": _gitlab_contributor_count(base_url, int(raw["id"]), headers)},
            )
        records.append(rec)
    return records


def _replace(rec: RepoRecord, patch: dict[str, Any]) -> RepoRecord:
    """Return a copy of ``rec`` with ``patch`` applied (drops ``None`` values)."""
    data = rec.as_dict()
    data.update({k: v for k, v in patch.items() if v is not None})
    return RepoRecord(**data)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render(records: list[RepoRecord], fmt: str = "md") -> str:
    """Render records as ``md`` (Markdown table), ``csv``, or ``json``."""
    if fmt == "json":
        return json.dumps([r.as_dict() for r in records], indent=2)
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(COLUMNS))
        writer.writeheader()
        for rec in records:
            writer.writerow(rec.as_row())
        return buf.getvalue()
    if fmt == "md":
        return _render_markdown(records)
    raise ValueError(f"unknown format: {fmt!r} (want md, csv, or json)")


def _render_markdown(records: list[RepoRecord]) -> str:
    def esc(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(COLUMNS) + " |",
        "| " + " | ".join("---" for _ in COLUMNS) + " |",
    ]
    for rec in records:
        row = rec.as_row()
        lines.append("| " + " | ".join(esc(row[c]) for c in COLUMNS) + " |")
    return "\n".join(lines) + "\n"


def sort_records(records: list[RepoRecord], key: str) -> list[RepoRecord]:
    """Sort a copy of ``records`` by ``key`` (name|created|updated|pushed|stars)."""
    if key == "stars":
        return sorted(records, key=lambda r: r.stars, reverse=True)
    if key in ("created", "updated", "pushed"):
        return sorted(records, key=lambda r: getattr(r, key), reverse=True)
    if key == "name":
        return sorted(records, key=lambda r: (r.platform, r.owner.lower(), r.name.lower()))
    raise ValueError(f"unknown sort key: {key!r}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

_TARGET_MISSING = object()  # sentinel: --github / --gitlab flag present, no value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo_inventory.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--github",
        nargs="?",
        const=_TARGET_MISSING,
        metavar="USER",
        help="include GitHub. Bare = your repos (needs token); with USER = that "
        "user's/org's repos.",
    )
    parser.add_argument(
        "--gitlab",
        nargs="?",
        const=_TARGET_MISSING,
        metavar="USER",
        help="include GitLab. Bare = projects you're a member of (needs token); "
        "with USER = that user's projects.",
    )
    parser.add_argument(
        "--gitlab-url",
        default=GITLAB_DEFAULT_URL,
        metavar="URL",
        help=f"GitLab base URL for self-managed instances (default: {GITLAB_DEFAULT_URL})",
    )
    parser.add_argument(
        "--github-token-env",
        default="GITHUB_TOKEN",
        metavar="VAR",
        help="env var holding the GitHub token (default: GITHUB_TOKEN)",
    )
    parser.add_argument(
        "--gitlab-token-env",
        default="GITLAB_TOKEN",
        metavar="VAR",
        help="env var holding the GitLab token (default: GITLAB_TOKEN)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="fetch contributors / watchers / has_readme via per-repo calls (slow)",
    )
    parser.add_argument(
        "--format",
        choices=("md", "csv", "json"),
        default="md",
        help="output format (default: md)",
    )
    parser.add_argument(
        "--sort",
        choices=("name", "created", "updated", "pushed", "stars"),
        default="pushed",
        help="sort order (default: pushed, most-recent first)",
    )
    parser.add_argument(
        "--exclude-forks", action="store_true", help="drop repos that are forks"
    )
    parser.add_argument(
        "--exclude-archived", action="store_true", help="drop archived repos"
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="write to FILE instead of stdout",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="suppress progress on stderr"
    )
    parser.add_argument(
        "--selftest", action="store_true", help="run built-in checks and exit"
    )
    return parser


def _resolve_target(value: Any, token_env: str) -> tuple[bool, str | None, str | None]:
    """Map a --github/--gitlab arg to (enabled, user, token)."""
    if value is None:
        return False, None, None
    token = os.environ.get(token_env) or None
    if value is _TARGET_MISSING:
        return True, None, token
    return True, str(value), token


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.selftest:
        return _selftest()

    gh_on, gh_user, gh_token = _resolve_target(args.github, args.github_token_env)
    gl_on, gl_user, gl_token = _resolve_target(args.gitlab, args.gitlab_token_env)
    if not gh_on and not gl_on:
        print("error: pass --github and/or --gitlab (see --help)", file=sys.stderr)
        return 2

    progress = None if args.quiet else (lambda msg: print(msg, file=sys.stderr))
    records: list[RepoRecord] = []
    try:
        if gh_on:
            if progress:
                progress("fetching GitHub...")
            records += fetch_github(
                token=gh_token, user=gh_user, full=args.full, progress=progress
            )
        if gl_on:
            if progress:
                progress("fetching GitLab...")
            records += fetch_gitlab(
                token=gl_token,
                user=gl_user,
                base_url=args.gitlab_url,
                full=args.full,
                progress=progress,
            )
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.exclude_forks:
        records = [r for r in records if not r.is_fork]
    if args.exclude_archived:
        records = [r for r in records if not r.archived]
    records = sort_records(records, args.sort)

    output = render(records, args.format)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(output, encoding="utf-8")
        if progress:
            progress(f"wrote {len(records)} repos to {args.output}")
    else:
        sys.stdout.write(output)
    return 0


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #


def _selftest() -> int:
    import doctest

    failures = doctest.testmod(verbose=False)[0]

    gh_sample = {
        "name": "widget",
        "full_name": "octocat/widget",
        "owner": {"login": "octocat"},
        "description": "a widget",
        "private": False,
        "visibility": "public",
        "fork": False,
        "archived": True,
        "language": "Python",
        "stargazers_count": 12,
        "forks_count": 3,
        "watchers_count": 12,
        "open_issues_count": 1,
        "topics": ["cli", "tools"],
        "license": {"spdx_id": "MIT", "name": "MIT License"},
        "default_branch": "main",
        "created_at": "2020-01-02T03:04:05Z",
        "updated_at": "2024-06-07T08:09:10Z",
        "pushed_at": "2024-06-06T00:00:00Z",
        "html_url": "https://github.com/octocat/widget",
    }
    r = github_repo_to_record(gh_sample)
    assert r.platform == "github" and r.owner == "octocat" and r.name == "widget"
    assert r.stars == 12 and r.forks == 3 and r.archived is True
    assert r.watchers is None  # not filled without --full
    assert r.license == "MIT"
    assert r.topics == ["cli", "tools"]
    assert r.created == "2020-01-02" and r.pushed == "2024-06-06"
    assert r.as_row()["archived"] == "yes"
    assert r.as_row()["watchers"] == "?"

    gl_sample = {
        "id": 42,
        "path": "gadget",
        "name": "Gadget",
        "namespace": {"full_path": "mygroup/sub"},
        "description": "",
        "visibility": "private",
        "archived": False,
        "forked_from_project": {"id": 7},
        "star_count": 0,
        "forks_count": 0,
        "open_issues_count": 5,
        "topics": ["data"],
        "license": {"nickname": "Apache 2.0"},
        "readme_url": "https://gitlab.com/mygroup/sub/gadget/-/blob/main/README.md",
        "default_branch": "main",
        "created_at": "2019-05-05T00:00:00.000Z",
        "last_activity_at": "2023-03-03T12:00:00.000Z",
        "web_url": "https://gitlab.com/mygroup/sub/gadget",
    }
    g = gitlab_repo_to_record(gl_sample)
    assert g.platform == "gitlab" and g.owner == "mygroup/sub" and g.name == "gadget"
    assert g.is_fork is True and g.open_issues == 5
    assert g.has_readme is True and g.license == "Apache 2.0"
    assert g.topics == ["data"]
    assert g.updated == "2023-03-03"
    assert g.as_row()["description"] == ""  # known-empty renders blank, not "?"

    # tag_list fallback when 'topics' key is absent
    g2 = gitlab_repo_to_record({"path": "x", "namespace": {}, "tag_list": ["a", "b"]})
    assert g2.topics == ["a", "b"]
    assert g2.has_readme is None  # readme_url absent -> unknown

    md = render([r, g], "md")
    assert md.startswith("| platform | owner | name |")
    assert "octocat" in md and "mygroup/sub" in md
    assert len(md.splitlines()) == 4  # header + separator + 2 rows

    csv_out = render([r], "csv")
    assert csv_out.splitlines()[0] == ",".join(COLUMNS)

    json_out = json.loads(render([r], "json"))
    assert json_out[0]["watchers"] is None and json_out[0]["stars"] == 12

    assert _replace(r, {"contributors": 4, "watchers": None}).contributors == 4
    assert _replace(r, {"watchers": None}).watchers is None

    assert [x.name for x in sort_records([r, g], "name")] == ["widget", "gadget"]
    assert sort_records([g, r], "stars")[0].name == "widget"

    assert _date("2024-06-07T08:09:10Z") == "2024-06-07"
    assert _date("") == "" and _date(None) == ""

    if failures:
        print(f"{failures} doctest failure(s)", file=sys.stderr)
        return 1
    print("selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
