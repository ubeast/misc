"""Tests for ``repo_inventory`` (GitHub + GitLab repo inventory).

Importable as ``repo_inventory`` thanks to ``tests/conftest.py`` putting the
tool directory on ``sys.path``. These tests never touch the network -- they
exercise the pure normalisation / parsing / rendering helpers and drive the CLI
only through paths that don't make HTTP calls (``--selftest``, ``--help``, the
"no target" error).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import repo_inventory as ri

SCRIPT = Path(__file__).resolve().parent.parent / "repo_inventory.py"


# --- Link header parsing --------------------------------------------------- #


def test_parse_link_header_basic():
    value = (
        '<https://api.github.com/x?page=2>; rel="next", '
        '<https://api.github.com/x?page=9>; rel="last"'
    )
    assert ri.parse_link_header(value) == {
        "next": "https://api.github.com/x?page=2",
        "last": "https://api.github.com/x?page=9",
    }


@pytest.mark.parametrize("value", [None, "", "garbage-without-rel"])
def test_parse_link_header_empty(value):
    assert ri.parse_link_header(value) == {}


def test_last_page_from_link():
    assert ri.last_page_from_link('<https://x?per_page=1&page=42>; rel="last"') == 42
    assert ri.last_page_from_link('<https://x?page=2>; rel="next"') is None
    assert ri.last_page_from_link(None) is None


# --- GitHub normalisation ------------------------------------------------- #


@pytest.fixture
def gh_repo() -> dict:
    return {
        "name": "widget",
        "full_name": "octocat/widget",
        "owner": {"login": "octocat"},
        "description": "a widget",
        "private": True,
        "visibility": "private",
        "fork": False,
        "archived": False,
        "language": "Python",
        "stargazers_count": 12,
        "forks_count": 3,
        "watchers_count": 12,
        "open_issues_count": 4,
        "topics": ["cli", "tools"],
        "license": {"spdx_id": "MIT", "name": "MIT License"},
        "default_branch": "main",
        "created_at": "2020-01-02T03:04:05Z",
        "updated_at": "2024-06-07T08:09:10Z",
        "pushed_at": "2024-06-06T00:00:00Z",
        "html_url": "https://github.com/octocat/widget",
    }


def test_github_repo_to_record(gh_repo):
    r = ri.github_repo_to_record(gh_repo)
    assert (r.platform, r.owner, r.name) == ("github", "octocat", "widget")
    assert r.visibility == "private"
    assert r.stars == 12 and r.forks == 3 and r.open_issues == 4
    assert r.topics == ["cli", "tools"]
    assert r.license == "MIT"
    assert r.created == "2020-01-02" and r.pushed == "2024-06-06"
    # watchers_count from the list endpoint is a stars alias -> deliberately dropped
    assert r.watchers is None
    assert r.has_readme is None and r.contributors is None


def test_github_missing_fields_are_safe():
    r = ri.github_repo_to_record({"name": "bare", "owner": {}})
    assert r.name == "bare" and r.owner == ""
    assert r.description == "" and r.license == "" and r.topics == []
    assert r.stars == 0 and r.visibility == "public"


def test_github_visibility_falls_back_to_private_flag():
    r = ri.github_repo_to_record({"name": "x", "owner": {}, "private": True})
    assert r.visibility == "private"


# --- GitLab normalisation ----------------------------------------------- #


@pytest.fixture
def gl_project() -> dict:
    return {
        "id": 42,
        "path": "gadget",
        "name": "Gadget",
        "namespace": {"full_path": "mygroup/sub"},
        "description": "",
        "visibility": "internal",
        "archived": True,
        "forked_from_project": {"id": 7},
        "star_count": 5,
        "forks_count": 1,
        "open_issues_count": 2,
        "topics": ["data"],
        "license": {"nickname": "Apache 2.0"},
        "readme_url": "https://gitlab.com/mygroup/sub/gadget/-/blob/main/README.md",
        "default_branch": "main",
        "created_at": "2019-05-05T00:00:00.000Z",
        "last_activity_at": "2023-03-03T12:00:00.000Z",
        "web_url": "https://gitlab.com/mygroup/sub/gadget",
    }


def test_gitlab_repo_to_record(gl_project):
    g = ri.gitlab_repo_to_record(gl_project)
    assert (g.platform, g.owner, g.name) == ("gitlab", "mygroup/sub", "gadget")
    assert g.visibility == "internal" and g.archived is True
    assert g.is_fork is True
    assert g.stars == 5 and g.forks == 1 and g.open_issues == 2
    assert g.license == "Apache 2.0"
    assert g.has_readme is True
    assert g.updated == "2023-03-03" and g.pushed == "2023-03-03"
    assert g.watchers is None  # GitLab has no watch concept


def test_gitlab_tag_list_fallback_and_unknown_readme():
    g = ri.gitlab_repo_to_record(
        {"path": "x", "namespace": {}, "tag_list": ["a", "b"]}
    )
    assert g.topics == ["a", "b"]
    assert g.has_readme is None  # no readme_url key -> unknown, not False


def test_gitlab_no_readme_url_means_false_when_key_present():
    g = ri.gitlab_repo_to_record(
        {"path": "x", "namespace": {}, "readme_url": None}
    )
    assert g.has_readme is False


# --- _replace (applying --full patches) --------------------------------- #


def test_replace_drops_none(gh_repo):
    r = ri.github_repo_to_record(gh_repo)
    patched = ri._replace(r, {"contributors": 9, "watchers": None, "has_readme": True})
    assert patched.contributors == 9
    assert patched.has_readme is True
    assert patched.watchers is None  # None in the patch does not overwrite
    assert patched.stars == r.stars  # untouched fields survive


# --- rendering --------------------------------------------------------- #


def test_render_markdown(gh_repo, gl_project):
    recs = [ri.github_repo_to_record(gh_repo), ri.gitlab_repo_to_record(gl_project)]
    md = ri.render(recs, "md")
    lines = md.splitlines()
    assert lines[0] == "| " + " | ".join(ri.COLUMNS) + " |"
    assert lines[1].count("---") == len(ri.COLUMNS)
    assert len(lines) == 4  # header + separator + 2 rows
    assert "octocat" in md and "mygroup/sub" in md
    # None -> "?", known-empty -> blank
    row = [ln for ln in lines if "widget" in ln][0]
    assert " ? " in row  # watchers / has_readme / contributors


def test_render_markdown_escapes_pipes():
    r = ri.github_repo_to_record(
        {"name": "x", "owner": {}, "description": "a | b\nc"}
    )
    body = ri.render([r], "md").splitlines()[2]
    assert "a \\| b c" in body


def test_render_csv_header_and_types(gh_repo):
    r = ri.github_repo_to_record(gh_repo)
    out = ri.render([r], "csv").splitlines()
    assert out[0] == ",".join(ri.COLUMNS)
    assert out[1].startswith("github,octocat,widget,")


def test_render_json_preserves_none(gh_repo):
    r = ri.github_repo_to_record(gh_repo)
    obj = json.loads(ri.render([r], "json"))
    assert obj[0]["watchers"] is None
    assert obj[0]["stars"] == 12
    assert obj[0]["topics"] == ["cli", "tools"]


def test_render_unknown_format_raises(gh_repo):
    with pytest.raises(ValueError):
        ri.render([ri.github_repo_to_record(gh_repo)], "xml")


# --- sorting ---------------------------------------------------------- #


def test_sort_records(gh_repo, gl_project):
    a = ri.github_repo_to_record(gh_repo)          # widget, 12 stars, pushed 2024-06-06
    b = ri.gitlab_repo_to_record(gl_project)       # gadget, 5 stars,  pushed 2023-03-03
    # "name" sorts by (platform, owner, name): github before gitlab
    assert [x.name for x in ri.sort_records([b, a], "name")] == ["widget", "gadget"]
    assert ri.sort_records([b, a], "stars")[0].name == "widget"
    assert ri.sort_records([b, a], "pushed")[0].name == "widget"
    with pytest.raises(ValueError):
        ri.sort_records([a], "bogus")


# --- date helper ---------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2024-06-07T08:09:10Z", "2024-06-07"),
        ("2024-06-07T08:09:10.123Z", "2024-06-07"),
        ("", ""),
        (None, ""),
        ("not-a-date", "not-a-date"),
    ],
)
def test_date(raw, expected):
    assert ri._date(raw) == expected


# --- CLI (no network) ---------------------------------------------- #


def test_cli_selftest():
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--selftest"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert "selftest OK" in out.stdout


def test_cli_requires_a_target():
    out = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 2
    assert "--github" in out.stderr


def test_cli_help_mentions_full():
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0
    assert "--full" in out.stdout
