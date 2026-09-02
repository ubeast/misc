"""Tests for the j4_archive_dbricks_folder tool.

The tool is a Databricks notebook - a single self-contained `.py` file that is
never imported in normal use. `tests/conftest.py` puts the tool directory on
`sys.path` so it can be imported here as `j4_archive_dbricks_folder`; the guard
at the bottom of the file (`if "dbutils" in globals()`) keeps the archive from
running on import, so the individual functions can be exercised directly.

Everything is faked - no network, no `dbutils`, no real GitLab. `git` is real
(it ships on dev machines and Databricks) and is run against a local bare repo.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import requests

import j4_archive_dbricks_folder as dwa


# --------------------------------------------------------------------------- fakes


class FakeResponse:
    """Stand-in for requests.Response covering only what the tool touches."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_body: Any = None,
        content: bytes = b"",
        text: str | None = None,
        links: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_body
        self._content = content
        self.links = links or {}
        if text is not None:
            self.text = text
        elif json_body is not None:
            self.text = json.dumps(json_body)
        else:
            self.text = content.decode("utf-8", "replace")

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, chunk_size: int = 1) -> Any:
        for i in range(0, len(self._content), max(1, chunk_size)):
            yield self._content[i : i + chunk_size]

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeWorkspaceSession:
    """Simulates the Databricks endpoints export_workspace_folder calls.

    `listings` maps a workspace path to the objects directly under it. `files`
    maps a workspace path to either raw bytes (a successful export) or a
    ready-made FakeResponse (to inject an error).
    """

    def __init__(
        self,
        listings: dict[str, list[dict[str, Any]]],
        files: dict[str, bytes | FakeResponse],
    ) -> None:
        self.listings = listings
        self.files = files

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: Any = None,
        stream: bool = False,
    ) -> FakeResponse:
        params = params or {}
        if url.endswith("/api/2.0/workspace/list"):
            return FakeResponse(json_body={"objects": self.listings.get(params["path"], [])})
        if url.endswith("/api/2.0/workspace/export"):
            entry = self.files[params["path"]]
            return entry if isinstance(entry, FakeResponse) else FakeResponse(content=entry)
        raise AssertionError(f"unexpected GET {url}")


def _report() -> Any:
    return dwa.ArchiveReport(
        source_folder="/Users/me@x.com/proj",
        workspace_url="https://example.cloud.databricks.com",
        author="Me <me@x.com>",
        run_started_utc="2026-08-28 00:00:00Z",
    )


NB = "NOTEBOOK"
PY = {"object_type": NB, "language": "PYTHON"}


# ------------------------------------------------------------------- small helpers


def test_authenticated_url_quotes_token_and_has_no_double_slash() -> None:
    url = dwa._authenticated_url("https://gitlab.example.com/group/proj.git", "p@ss/w o+rd")
    assert url == "https://oauth2:p%40ss%2Fw%20o%2Brd@gitlab.example.com/group/proj.git"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, "MAX_NOTEBOOK_SIZE_EXCEEDED: too big", True),
        (413, "Payload Too Large", True),
        (400, "RESOURCE_DOES_NOT_EXIST", False),
        (500, "MAX_NOTEBOOK_SIZE_EXCEEDED", False),
    ],
)
def test_is_export_size_error(status: int, body: str, expected: bool) -> None:
    err = requests.HTTPError(response=FakeResponse(status_code=status, text=body))
    assert dwa._is_export_size_error(err) is expected


# --------------------------------------------------------------- workspace listing


def test_list_workspace_objects_follows_pagination() -> None:
    class Paged:
        def __init__(self) -> None:
            self.seen: list[dict[str, Any]] = []

        def get(self, url: str, params: dict[str, Any], timeout: Any) -> FakeResponse:
            self.seen.append(dict(params))
            if params.get("page_token") == "tok2":
                return FakeResponse(json_body={"objects": [{"path": "/c"}]})
            return FakeResponse(
                json_body={
                    "objects": [{"path": "/a"}, {"path": "/b"}],
                    "next_page_token": "tok2",
                }
            )

    session = Paged()
    got = list(dwa._list_workspace_objects("https://api", session, "/root"))
    assert [o["path"] for o in got] == ["/a", "/b", "/c"]
    assert session.seen[1]["page_token"] == "tok2"


# ------------------------------------------------------------------- export folder


def _tree_session() -> FakeWorkspaceSession:
    return FakeWorkspaceSession(
        listings={
            "/w/proj": [
                {"path": "/w/proj/main", **PY},
                {"path": "/w/proj/query", "object_type": NB, "language": "SQL"},
                {"path": "/w/proj/data.csv", "object_type": "FILE", "size": 8},
                {"path": "/w/proj/sub", "object_type": "DIRECTORY"},
                {"path": "/w/proj/archiver", **PY},
                {"path": "/w/proj/dash", "object_type": "DASHBOARD"},
                {"path": "/w/proj/empty", "object_type": "DIRECTORY"},
            ],
            "/w/proj/sub": [{"path": "/w/proj/sub/util", **PY}],
            "/w/proj/empty": [],
        },
        files={
            "/w/proj/main": b"# Databricks notebook source\nprint(1)\n",
            "/w/proj/query": b"-- Databricks notebook source\nSELECT 1\n",
            "/w/proj/data.csv": b"a,b\n1,2\n",
            "/w/proj/sub/util": b"# Databricks notebook source\n",
        },
    )


def test_export_workspace_folder_writes_tree_and_reports(tmp_path: Path) -> None:
    report = _report()
    dest = tmp_path / "proj"
    count = dwa.export_workspace_folder(
        "https://api",
        _tree_session(),
        "/w/proj",
        dest,
        {"/w/proj/archiver"},
        report,
    )

    assert count == 4  # main.py, query.sql, data.csv, sub/util.py  (archiver excluded)
    assert (dest / "main.py").read_bytes().startswith(b"# Databricks notebook source")
    assert (dest / "query.sql").exists()
    assert (dest / "data.csv").exists()
    assert (dest / "sub" / "util.py").exists()
    assert not (dest / "archiver.py").exists()

    # empty dir preserved, non-empty dirs not touched
    assert (dest / "empty" / ".gitkeep").exists()
    assert not (dest / "sub" / ".gitkeep").exists()
    assert report.empty_dirs_preserved == 1

    # unsupported object recorded, not fatal
    assert any("DASHBOARD" in s for s in report.skipped_objects)
    assert report.deviations == []


def test_export_workspace_folder_oversize_notebook_stops(tmp_path: Path) -> None:
    session = FakeWorkspaceSession(
        listings={"/w/proj": [{"path": "/w/proj/big", **PY}]},
        files={"/w/proj/big": FakeResponse(status_code=400, text="MAX_NOTEBOOK_SIZE_EXCEEDED")},
    )
    with pytest.raises(RuntimeError, match="exceeds the 10 MB workspace export limit"):
        dwa.export_workspace_folder(
            "https://api", session, "/w/proj", tmp_path / "p", set(), _report()
        )


def test_export_file_falls_back_to_fuse_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fuse_root = tmp_path / "Workspace"
    (fuse_root / "w" / "proj").mkdir(parents=True)
    (fuse_root / "w" / "proj" / "model.bin").write_bytes(b"x" * 4096)
    monkeypatch.setattr(dwa, "WORKSPACE_FUSE_ROOT", fuse_root)

    session = FakeWorkspaceSession(
        listings={"/w/proj": [{"path": "/w/proj/model.bin", "object_type": "FILE"}]},
        files={"/w/proj/model.bin": FakeResponse(status_code=413, text="too large")},
    )
    report = _report()
    dest = tmp_path / "out"
    count = dwa.export_workspace_folder("https://api", session, "/w/proj", dest, set(), report)
    assert count == 1
    assert (dest / "model.bin").read_bytes() == b"x" * 4096
    assert len(report.deviations) == 1
    assert "filesystem mount" in report.deviations[0]


def test_export_file_fails_clearly_when_fuse_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dwa, "WORKSPACE_FUSE_ROOT", tmp_path / "no-such-mount")
    session = FakeWorkspaceSession(
        listings={"/p": [{"path": "/p/x.bin", "object_type": "FILE"}]},
        files={"/p/x.bin": FakeResponse(status_code=413, text="too large")},
    )
    with pytest.raises(RuntimeError, match="Copy it to a Unity Catalog volume"):
        dwa.export_workspace_folder("https://api", session, "/p", tmp_path / "o", set(), _report())


# ---------------------------------------------------------------------- user lookup


def test_get_user_details_uses_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dwa, "AUTHOR_NAME_OVERRIDE", "Set Name")
    monkeypatch.setattr(dwa, "AUTHOR_EMAIL_OVERRIDE", "set@x.com")
    assert dwa.get_user_details("https://api", None) == ("Set Name", "set@x.com")


def test_get_user_details_reads_scim() -> None:
    class Scim:
        def get(self, url: str, timeout: Any) -> FakeResponse:
            return FakeResponse(
                json_body={
                    "userName": "me@x.com",
                    "displayName": "Me X",
                    "emails": [
                        {"value": "alt@x.com", "primary": False},
                        {"value": "me@x.com", "primary": True},
                    ],
                }
            )

    assert dwa.get_user_details("https://api", Scim()) == ("Me X", "me@x.com")


def test_get_user_details_raises_without_email() -> None:
    class Scim:
        def get(self, url: str, timeout: Any) -> FakeResponse:
            return FakeResponse(json_body={"userName": "", "emails": []})

    with pytest.raises(RuntimeError, match="Could not determine the running user"):
        dwa.get_user_details("https://api", Scim())


# ------------------------------------------------------------------ project create


_TAKEN = '{"message":{"name":["has already been taken"]}}'
_BAD_NAME = '{"message":{"name":["can contain only letters, digits, ..."]}}'
_TODAY = "2026-08-31"


class _FakeGitLab:
    """Project create backed by an in-memory set of names that already exist.

    `invalid` names get GitLab's "invalid name" 400; anything already in
    `existing` (or created in an earlier call) gets the "already taken" 400.
    """

    def __init__(self, existing: list[str], invalid: set[str] | None = None) -> None:
        self.existing = set(existing)
        self.invalid = invalid or set()
        self.created: list[str] = []
        self.post_calls = 0

    def post(self, url: str, json: Any = None, timeout: Any = None) -> FakeResponse:
        self.post_calls += 1
        name = json["name"]
        if name in self.invalid:
            return FakeResponse(status_code=400, text=_BAD_NAME)
        if name in self.existing:
            return FakeResponse(status_code=400, text=_TAKEN)
        self.existing.add(name)
        self.created.append(name)
        return FakeResponse(status_code=201, json_body={"id": len(self.created), "web_url": "u"})


def test_candidate_project_names_sequence() -> None:
    it = dwa._candidate_project_names("proj", "2026-08-31")
    assert [next(it) for _ in range(4)] == [
        "proj_2026-08-31",
        "proj_2026-08-31_2",
        "proj_2026-08-31_3",
        "proj_2026-08-31_4",
    ]


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("customer-churn", "customer-churn"),  # already fine - untouched
        ("Customer Churn Model", "Customer Churn Model"),  # spaces kept
        ("café-analytics", "café-analytics"),  # accented letters kept
        ("A&B analysis", "AB analysis"),  # '&' stripped
        ("sprint #3", "sprint 3"),  # '#' stripped
        ("Q3 report (final)!", "Q3 report final"),  # '(', ')', '!' all stripped
        ("a+b-c.d", "a+b-c.d"),  # '+', '-', '.' kept
        (".scratch", "scratch"),  # leading '.' dropped
        ("-wip", "wip"),  # leading '-' dropped
        ("report.", "report"),  # trailing '.' dropped
        ("  spaced  ", "spaced"),  # surrounding whitespace dropped
        ("###", ""),  # nothing usable
        ("!@#$%", ""),  # nothing usable
    ],
)
def test_sanitize_project_name(raw: str, cleaned: str) -> None:
    assert dwa._sanitize_project_name(raw) == cleaned


def test_create_gitlab_project_names_with_todays_date() -> None:
    gl = _FakeGitLab(existing=[])
    project, name = dwa.create_gitlab_project(gl, "proj", _report(), today=_TODAY)
    assert name == "proj_2026-08-31"
    assert project["id"] == 1
    assert gl.post_calls == 1


def test_create_gitlab_project_same_day_repeat_gets_a_counter() -> None:
    gl = _FakeGitLab(existing=["proj_2026-08-31"])
    _project, name = dwa.create_gitlab_project(gl, "proj", _report(), today=_TODAY)
    assert name == "proj_2026-08-31_2"
    assert gl.post_calls == 2  # dated name (taken), then _2 (ok)


def test_create_gitlab_project_walks_past_several_same_day_archives() -> None:
    gl = _FakeGitLab(existing=["proj_2026-08-31", "proj_2026-08-31_2", "proj_2026-08-31_3"])
    _project, name = dwa.create_gitlab_project(gl, "proj", _report(), today=_TODAY)
    assert name == "proj_2026-08-31_4"
    assert gl.post_calls == 4


def test_create_gitlab_project_different_day_never_collides() -> None:
    # yesterday's archive exists; today's run does not touch it
    gl = _FakeGitLab(existing=["proj_2026-08-30"])
    _project, name = dwa.create_gitlab_project(gl, "proj", _report(), today=_TODAY)
    assert name == "proj_2026-08-31"
    assert gl.post_calls == 1


def test_create_gitlab_project_uses_utc_today_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dwa, "_utc_today", lambda: "2026-12-25")
    _project, name = dwa.create_gitlab_project(_FakeGitLab(existing=[]), "proj", _report())
    assert name == "proj_2026-12-25"


def test_create_gitlab_project_gives_up_after_max_same_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dwa, "MAX_SAME_DAY_ARCHIVES", 3)
    gl = _FakeGitLab(
        existing=["proj_2026-08-31", "proj_2026-08-31_2", "proj_2026-08-31_3"]
    )
    with pytest.raises(RuntimeError, match="already been archived 3 times today"):
        dwa.create_gitlab_project(gl, "proj", _report(), today=_TODAY)


def test_create_gitlab_project_other_error_raises() -> None:
    class GL:
        def post(self, url: str, json: Any = None, timeout: Any = None) -> FakeResponse:
            return FakeResponse(status_code=403, text="forbidden")

    with pytest.raises(RuntimeError, match="project creation failed: 403"):
        dwa.create_gitlab_project(GL(), "proj", _report(), today=_TODAY)


def test_create_gitlab_project_cleans_a_name_gitlab_rejects() -> None:
    gl = _FakeGitLab(existing=[], invalid={"A&B analysis_2026-08-31"})
    report = _report()
    _project, name = dwa.create_gitlab_project(gl, "A&B analysis", report, today=_TODAY)
    assert name == "AB analysis_2026-08-31"
    assert gl.post_calls == 2  # rejected raw name, then the cleaned one
    assert report.deviations and "A&B analysis" in report.deviations[0]


def test_create_gitlab_project_clean_then_same_day_counter() -> None:
    gl = _FakeGitLab(
        existing=["AB analysis_2026-08-31"], invalid={"A&B analysis_2026-08-31"}
    )
    _project, name = dwa.create_gitlab_project(gl, "A&B analysis", _report(), today=_TODAY)
    assert name == "AB analysis_2026-08-31_2"


def test_create_gitlab_project_raises_when_nothing_usable_after_cleaning() -> None:
    gl = _FakeGitLab(existing=[], invalid={"###_2026-08-31"})
    with pytest.raises(RuntimeError, match="leaves nothing usable"):
        dwa.create_gitlab_project(gl, "###", _report(), today=_TODAY)


def test_create_gitlab_project_raises_when_cleaned_name_also_rejected() -> None:
    gl = _FakeGitLab(existing=[], invalid={"A&B_2026-08-31", "AB_2026-08-31"})
    with pytest.raises(RuntimeError, match="project creation failed: 400"):
        dwa.create_gitlab_project(gl, "A&B", _report(), today=_TODAY)


# -------------------------------------------------------------------- verification


def test_validate_push_detects_missing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dwa.time, "sleep", lambda _s: None)
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref")

    class GL:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url: str, params: Any, timeout: Any) -> FakeResponse:
            self.calls += 1
            return FakeResponse(json_body=[{"type": "blob", "path": "a.py"}])

    gl = GL()
    report = _report()
    dwa.validate_push(gl, 1, tmp_path, report)
    assert report.missing_on_remote == ["sub/b.py"]
    assert "MISSING" in report.verification
    assert gl.calls == dwa.TREE_RECHECK_ATTEMPTS  # genuinely missing -> retried to the limit


def test_validate_push_all_present(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x")

    class GL:
        def get(self, url: str, params: Any, timeout: Any) -> FakeResponse:
            return FakeResponse(json_body=[{"type": "blob", "path": "a.py"}])

    report = _report()
    dwa.validate_push(GL(), 1, tmp_path, report)
    assert report.missing_on_remote == []
    assert report.is_incomplete is False


def test_validate_push_retries_past_a_lagging_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dwa.time, "sleep", lambda _s: None)
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.py").write_text("y")

    class GL:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url: str, params: Any, timeout: Any) -> FakeResponse:
            self.calls += 1
            blobs = [{"type": "blob", "path": "a.py"}]
            if self.calls >= 2:  # tree "catches up" on the second look
                blobs.append({"type": "blob", "path": "b.py"})
            return FakeResponse(json_body=blobs)

    gl = GL()
    report = _report()
    dwa.validate_push(gl, 1, tmp_path, report)
    assert report.missing_on_remote == []
    assert report.is_incomplete is False
    assert gl.calls == 2


# --------------------------------------------------------------------- notes file


def test_render_archive_notes_complete() -> None:
    report = _report()
    report.project_name = "proj_2026-08-31"
    report.exported_file_count = 12
    report.empty_dirs_preserved = 2
    report.verification = "all 12 exported files present on remote"
    text = dwa.render_archive_notes(report)
    assert "| Archive project | `proj_2026-08-31` |" in text
    assert "## Status: COMPLETE" in text
    assert "INCOMPLETE" not in text
    assert "Every exported file was verified present on the remote" in text
    assert "does NOT contain" in text
    assert "/Users/me@x.com/proj" in text
    assert "Restoring this archive" in text


def test_render_archive_notes_incomplete_and_deviations() -> None:
    report = _report()
    report.missing_on_remote = ["big/thing.bin"]
    report.deviations = ["`/p/x` exceeded the 10 MB export API limit; archived from the mount."]
    report.skipped_objects = ["`/p/exp` (MLFLOW_EXPERIMENT)"]
    text = dwa.render_archive_notes(report)
    assert "## Status: INCOMPLETE" in text
    assert "did not verify on the remote" in text
    assert "big/thing.bin" in text
    assert "## Handled specially" in text
    assert "Workspace objects not archived" in text


def _sample_complete_report() -> Any:
    report = dwa.ArchiveReport(
        source_folder="/Users/dev@example.gov/customer-churn",
        workspace_url="https://example-prod.cloud.databricks.com",
        author="Dev Example <dev@example.gov>",
        run_started_utc="2026-08-28 15:22:04Z",
        project_name="customer-churn_2026-08-28",
    )
    report.exported_file_count = 15
    report.empty_dirs_preserved = 1
    report.verification = "all 16 files present on remote (15 archived + 1 .gitkeep placeholder(s))"
    return report


def _sample_incomplete_report() -> Any:
    report = dwa.ArchiveReport(
        source_folder="/Users/dev@example.gov/R&D pricing model",
        workspace_url="https://example-prod.cloud.databricks.com",
        author="Dev Example <dev@example.gov>",
        run_started_utc="2026-08-28 16:05:41Z",
        project_name="RD pricing model_2026-08-28",
    )
    report.exported_file_count = 38
    report.empty_dirs_preserved = 3
    report.verification = (
        "1 of 42 file(s) (38 archived + 3 .gitkeep placeholder(s)) MISSING on remote"
    )
    report.missing_on_remote = ["reference/historical_rates_2019.parquet"]
    report.deviations = [
        (
            "GitLab does not accept 'R&D pricing model' as a project name; this archive "
            "is named `RD pricing model_2026-08-28` (disallowed characters stripped). "
            "The source folder is unchanged."
        ),
        (
            "`/Users/dev@example.gov/R&D pricing model/reference/rate_table.parquet` "
            "exceeded the 10 MB export API limit; archived by reading it directly from the "
            "/Workspace filesystem mount."
        ),
    ]
    report.skipped_objects = [
        "`/Users/dev@example.gov/R&D pricing model/tuning-runs` (MLFLOW_EXPERIMENT)"
    ]
    return report


@pytest.mark.parametrize(
    ("filename", "builder"),
    [
        ("sample_archive_notes_complete.md", _sample_complete_report),
        ("sample_archive_notes_incomplete.md", _sample_incomplete_report),
    ],
)
def test_render_matches_committed_sample(filename: str, builder: Any) -> None:
    """The committed samples in tests/ must match what the tool produces.

    If this fails after a deliberate wording change, regenerate them:
        python tests/regenerate_samples.py
    """
    expected = (Path(__file__).resolve().parent / filename).read_text(encoding="utf-8")
    assert dwa.render_archive_notes(builder()) == expected


# ---------------------------------------------------------------------------- git


def test_push_to_gitlab_commits_with_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    monkeypatch.setattr(dwa, "_authenticated_url", lambda url, token: url)

    work = tmp_path / "work"
    work.mkdir()
    (work / "a.py").write_text("print('hi')\n")

    dwa.push_to_gitlab(work, str(bare), "tok", "Test User", "test@example.com")

    log = subprocess.run(
        ["git", "-C", str(bare), "log", "--format=%an|%ae|%s"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.strip() == "Test User|test@example.com|Archive from Databricks workspace"


def test_git_runner_scrubs_token_from_errors(tmp_path: Path) -> None:
    git = dwa._git_runner(tmp_path, "SUPERSECRET")
    with pytest.raises(RuntimeError) as excinfo:
        git(["push", "https://oauth2:SUPERSECRET@host/x.git", "main:main"])
    message = str(excinfo.value)
    assert "SUPERSECRET" not in message
    assert "***" in message


# --------------------------------------------------------------- download safety


def test_export_file_rejects_short_download(tmp_path: Path) -> None:
    session = FakeWorkspaceSession(
        listings={"/p": [{"path": "/p/data.bin", "object_type": "FILE", "size": 100}]},
        files={"/p/data.bin": b"only ten!!"},  # 10 bytes, not 100
    )
    with pytest.raises(RuntimeError, match="is 10 bytes but Databricks reported 100"):
        dwa.export_workspace_folder("https://api", session, "/p", tmp_path / "o", set(), _report())


def test_stream_export_retries_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dwa.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    class Flaky:
        def get(self, url: str, params: Any, timeout: Any, stream: bool) -> FakeResponse:
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.ConnectionError("boom")
            return FakeResponse(content=b"payload")

    dest = tmp_path / "f.bin"
    dwa._stream_export("https://api", Flaky(), "/p/f", "AUTO", dest)
    assert dest.read_bytes() == b"payload"
    assert not dest.with_name("f.bin.part").exists()
    assert calls["n"] == 3


def test_stream_export_gives_up_after_max_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dwa.time, "sleep", lambda _s: None)

    class Down:
        def get(self, url: str, params: Any, timeout: Any, stream: bool) -> FakeResponse:
            raise requests.ConnectionError("boom")

    dest = tmp_path / "f.bin"
    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        dwa._stream_export("https://api", Down(), "/p/f", "AUTO", dest)
    assert not dest.exists()
    assert not dest.with_name("f.bin.part").exists()


# ------------------------------------------------------------- project description


@pytest.mark.parametrize(
    ("missing", "deviations", "fragment"),
    [
        ([], [], "Databricks workspace archive. See"),
        ([], ["something odd"], "something handled specially"),
        (["x/y.bin"], [], "INCOMPLETE ARCHIVE"),
    ],
)
def test_set_project_description_levels(
    missing: list[str], deviations: list[str], fragment: str
) -> None:
    report = _report()
    report.missing_on_remote = missing
    report.deviations = deviations
    sent: dict[str, Any] = {}

    class GL:
        def put(self, url: str, json: Any, timeout: Any) -> FakeResponse:
            sent.update(json)
            return FakeResponse(status_code=200, json_body={})

    dwa.set_project_description(GL(), 1, report)
    assert fragment in sent["description"]


# -------------------------------------------------------------------- end to end


def _repo_blobs(bare: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(bare), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


class _Opt:
    def __init__(self, value: Any) -> None:
        self._value = value

    def get(self) -> Any:
        return self._value

    def isDefined(self) -> bool:
        return self._value is not None


class _FakeDbutils:
    """The slice of the notebook `dbutils` global that the tool reads."""

    def __init__(self, notebook_path: str) -> None:
        self._notebook_path = notebook_path

    # dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    @property
    def notebook(self) -> Any:
        return self

    @property
    def entry_point(self) -> Any:
        return self

    def getDbutils(self) -> Any:
        return self

    def __call__(self) -> Any:
        return self

    def getContext(self) -> Any:
        outer = self

        class Ctx:
            def apiUrl(self) -> _Opt:
                return _Opt("https://api.test")

            def apiToken(self) -> _Opt:
                return _Opt("db-token")

            def notebookPath(self) -> _Opt:
                return _Opt(outer._notebook_path)

            def tags(self) -> Any:
                return type("Tags", (), {"get": lambda self, k: _Opt(None)})()

        return Ctx()

    @property
    def secrets(self) -> Any:
        return type("Secrets", (), {"get": lambda self, scope, key: "gl-pat"})()


@pytest.mark.parametrize(
    ("folder", "reject", "project_leaf", "deviation_marker"),
    [
        ("proj", set(), "proj_2026-08-31", None),
        ("R&D proj", {"R&D proj_2026-08-31"}, "RD proj_2026-08-31", "R&D proj"),
    ],
)
def test_archive_workspace_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    folder: str,
    reject: set[str],
    project_leaf: str,
    deviation_marker: str | None,
) -> None:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)

    root = f"/Users/me/{folder}"
    workspace = {
        root: [
            {"path": f"{root}/run", **PY},
            {"path": f"{root}/data.csv", "object_type": "FILE", "size": 6},
            {"path": f"{root}/archiver", **PY},
            {"path": f"{root}/empty", "object_type": "DIRECTORY"},
        ],
        f"{root}/empty": [],
    }
    exports = {
        f"{root}/run": b"# Databricks notebook source\nx=1\n",
        f"{root}/data.csv": b"a,b\n1\n",
    }

    class Session:
        def __init__(self) -> None:
            self.descriptions: list[str] = []

        def get(
            self, url: str, params: Any = None, timeout: Any = None, stream: bool = False
        ) -> FakeResponse:
            params = params or {}
            if url.endswith("/scim/v2/Me"):
                return FakeResponse(
                    json_body={"displayName": "Me", "emails": [{"value": "me@x", "primary": True}]}
                )
            if url.endswith("/workspace/list"):
                return FakeResponse(json_body={"objects": workspace.get(params["path"], [])})
            if url.endswith("/workspace/export"):
                return FakeResponse(content=exports[params["path"]])
            if url.endswith("/repository/tree"):
                return FakeResponse(
                    json_body=[{"type": "blob", "path": p} for p in _repo_blobs(bare)]
                )
            if "/repository/files/" in url:
                return FakeResponse(status_code=200, json_body={"file_name": dwa.NOTES_FILENAME})
            raise AssertionError(f"unexpected GET {url}")

        def post(self, url: str, json: Any = None, timeout: Any = None) -> FakeResponse:
            if url.endswith("/api/v4/projects"):
                if json["name"] in reject:
                    return FakeResponse(
                        status_code=400, text='{"message":{"name":["is invalid"]}}'
                    )
                return FakeResponse(
                    status_code=201,
                    json_body={
                        "id": 1,
                        "web_url": "https://gl/proj",
                        "http_url_to_repo": str(bare),
                    },
                )
            raise AssertionError(f"unexpected POST {url}")

        def put(self, url: str, json: Any = None, timeout: Any = None) -> FakeResponse:
            self.descriptions.append(json["description"])
            return FakeResponse(status_code=200, json_body={})

    session = Session()
    monkeypatch.setattr(dwa, "_session", lambda _headers: session)
    monkeypatch.setattr(dwa, "_authenticated_url", lambda url, _token: url)
    monkeypatch.setattr(dwa, "_utc_today", lambda: "2026-08-31")

    dwa.archive_workspace(_FakeDbutils(f"{root}/archiver"))

    subjects = subprocess.run(
        ["git", "-C", str(bare), "log", "--format=%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    assert dwa.COMMIT_MESSAGE in subjects
    assert dwa.NOTES_COMMIT_MESSAGE in subjects

    blobs = _repo_blobs(bare)
    assert "run.py" in blobs
    assert "data.csv" in blobs
    assert "empty/.gitkeep" in blobs
    assert "ARCHIVE_NOTES.md" in blobs
    assert "archiver.py" not in blobs  # the archiver excludes itself

    notes = subprocess.run(
        ["git", "-C", str(bare), "show", f"main:{dwa.NOTES_FILENAME}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # the resolved (possibly cleaned) project name is recorded in the notes
    assert f"| Archive project | `{project_leaf}` |" in notes
    if deviation_marker:
        assert "## Handled specially" in notes
        assert deviation_marker in notes
        assert session.descriptions[-1].startswith(
            "Databricks workspace archive - something handled specially"
        )
    else:
        assert session.descriptions[-1].startswith("Databricks workspace archive. See")
