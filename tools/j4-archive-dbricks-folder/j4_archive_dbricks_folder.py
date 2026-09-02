# Databricks notebook source
"""Archive the Databricks workspace folder this file lives in to a new GitLab project.

This file is a Databricks notebook (the marker line above tells Databricks so).
It runs only inside Databricks - it needs the `dbutils` runtime global and a
notebook execution context to find the folder it is running from. It does not run
as a plain `python` script.

Run it from inside the folder you want to archive: open it as a notebook and
"Run all", or point a job notebook-task at it. It:

  1. Recursively exports that folder - every notebook, file and sub-folder,
     EXCEPT this file - to a temp directory. Empty sub-folders get a `.gitkeep`
     so git keeps them. Non-notebook files over the 10 MB export-API limit are
     read straight off the `/Workspace` filesystem mount instead; a notebook over
     that limit stops the run with instructions.
  2. Creates a new private GitLab project in the group set by ARCHIVE_GROUP_ID,
     named `<folder>_<UTC date>` (e.g. `proj_2026-08-31`). Re-archiving the same
     folder is fine - each run is its own dated project; a second run on the same
     day gets a `_2`, `_3`, ... counter. If GitLab will not accept the folder
     name, the characters it rejects are stripped out for the project name only.
  3. Pushes the export as one commit on `main`.
  4. Checks every exported file made it to the remote.
  5. Writes `ARCHIVE_NOTES.md` into the project - provenance, what verified, what
     was skipped or handled unusually, what an archive like this does NOT contain,
     and how to restore it - as a second commit.

The original workspace folder is never modified. To browse the archive inside
Databricks afterwards, add it as a Git folder by hand: Repos > Add Repo, and
paste the project URL the run prints.

`dbutils` is only used at the very bottom and in get_databricks_context() /
get_gitlab_token(); everything else is plain functions.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

if TYPE_CHECKING:
    # dbutils is injected into every notebook by the Databricks runtime. This
    # tells the type checker and linter it exists; it is never assigned here.
    dbutils: Any


# --- CONFIG: edit here ------------------------------------------------------
GITLAB_URL = "https://gitlab.advana.data.mil"
GITLAB_SECRET_SCOPE = "mschertz"
GITLAB_SECRET_KEY = "gitlab_token"

# GitLab group ID that each run creates its new archive project in.
# Default 12623 = the group used for retained pre-migration code in Advana at the
# time of writing. This is only a default - point it at any other group's ID to
# send archives there instead (GitLab: open the group -> "..." menu -> "Copy
# group ID").
ARCHIVE_GROUP_ID = 12623

HTTP_TIMEOUT = 30  # seconds, per request
COMMIT_MESSAGE = "Archive from Databricks workspace"
NOTES_COMMIT_MESSAGE = "Add ARCHIVE_NOTES.md (archive record and verification)"

# Git commit author. Leave both blank to use whoever runs this notebook
# (looked up from Databricks). Set both to attribute the commit to someone else.
AUTHOR_NAME_OVERRIDE = ""
AUTHOR_EMAIL_OVERRIDE = ""
# --- end CONFIG ----------------------------------------------------------------

EXT_BY_LANGUAGE = {"PYTHON": ".py", "SQL": ".sql", "SCALA": ".scala", "R": ".r"}

# Identifies the tool in every ARCHIVE_NOTES.md. There is deliberately no version
# constant - the tool's git history is the version record, and each archive is
# stamped with its run timestamp.
TOOL_NAME = "j4_archive_dbricks_folder"
TOOL_LOCATION = "the ubeast/misc repo, tools/j4-archive-dbricks-folder/"

NOTES_FILENAME = "ARCHIVE_NOTES.md"
GITKEEP_FILENAME = ".gitkeep"

# Every archive project is named `<folder>_<UTC date>`, e.g. `proj_2026-08-31`.
# A second archive of the same folder on the same day appends a counter:
# `proj_2026-08-31_2`, `proj_2026-08-31_3`, ... Different days never collide.
MAX_SAME_DAY_ARCHIVES = 99  # stop if a folder is archived this many times in one day

# Characters kept as-is in a project name when the folder name has to be cleaned
# because GitLab rejected it. Letters (any script) and digits are kept via
# str.isalnum(); these are the extra characters GitLab's project-name rule also
# allows - space, '_', '.', '+', '-'. GitLab's regex is
#   \A[\p{Alnum}©-\u{1f9ff}_][\p{Alnum}\p{Pd}+©-\u{1f9ff}_. ]*\z
# ("letters, digits, emoji, '_', '.', '+', dashes, or spaces; must start with a
# letter, digit, emoji, or '_'"). Note it does NOT allow parentheses, '&', '#'
# etc. Everything not kept is stripped out (see _sanitize_project_name).
PROJECT_NAME_EXTRA_CHARS = frozenset(" _.+-")

DOWNLOAD_CHUNK_BYTES = 1 << 20  # 1 MiB streaming chunk
DOWNLOAD_ATTEMPTS = 3  # retries for a download that drops mid-stream
TREE_RECHECK_ATTEMPTS = 3  # re-checks before believing a file is missing on the remote

# Databricks mounts the workspace tree here inside the runtime. Non-notebook
# files that are too big for the export API are read from this path instead.
WORKSPACE_FUSE_ROOT = Path("/Workspace")


def _session(auth_header: dict[str, str]) -> requests.Session:
    """A requests session that retries 429 / 5xx a few times with backoff."""
    s = requests.Session()
    s.headers.update(auth_header)
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class ArchiveReport:
    """What happened in one run, rendered into ARCHIVE_NOTES.md.

    The file is written into the archive itself so the record travels with it: a
    person opening the GitLab project years later can see where it came from,
    whether every file verified on the remote, what was skipped, and what a
    snapshot like this never contained.
    """

    source_folder: str
    workspace_url: str
    author: str
    run_started_utc: str
    project_name: str = ""
    exported_file_count: int = 0
    empty_dirs_preserved: int = 0
    skipped_objects: list[str] = field(default_factory=list)
    deviations: list[str] = field(default_factory=list)
    verification: str = "not run"
    missing_on_remote: list[str] = field(default_factory=list)

    @property
    def is_incomplete(self) -> bool:
        """True when a file that was exported did not verify on the remote."""
        return bool(self.missing_on_remote)


def get_databricks_context(dbutils: Any) -> dict[str, str]:
    """API URL, token, this notebook's path, and the browser workspace URL.

    `api_url` is where the REST API is served; on some deployments that host
    differs from the one a person opens in a browser, so `workspace_url` prefers
    the context's `browserHostName` tag and only falls back to `api_url`.
    """
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    api_url = str(ctx.apiUrl().get())
    try:
        host = ctx.tags().get("browserHostName")
        workspace_url = f"https://{host.get()}" if host.isDefined() else api_url
    except Exception:  # noqa: BLE001 - context internals vary by runtime; the fallback is fine
        workspace_url = api_url
    return {
        "api_url": api_url,
        "api_token": str(ctx.apiToken().get()),
        "notebook_path": str(ctx.notebookPath().get()),
        "workspace_url": workspace_url,
    }


def get_gitlab_token(dbutils: Any) -> str:
    return str(dbutils.secrets.get(scope=GITLAB_SECRET_SCOPE, key=GITLAB_SECRET_KEY))


def get_user_details(api_url: str, db: requests.Session) -> tuple[str, str]:
    """(name, email) for the git commit author.

    Uses AUTHOR_*_OVERRIDE if both are set, otherwise the running user from the
    Databricks SCIM 'Me' endpoint.
    """
    if AUTHOR_NAME_OVERRIDE and AUTHOR_EMAIL_OVERRIDE:
        return AUTHOR_NAME_OVERRIDE, AUTHOR_EMAIL_OVERRIDE

    r = db.get(f"{api_url}/api/2.0/preview/scim/v2/Me", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    body: dict[str, Any] = r.json()
    username: str = body.get("userName") or ""
    name: str = body.get("displayName") or username
    email: str = next((e["value"] for e in body.get("emails", []) if e.get("primary")), username)
    if not email:
        raise RuntimeError(
            "Could not determine the running user. Set AUTHOR_NAME_OVERRIDE and "
            "AUTHOR_EMAIL_OVERRIDE at the top of this file."
        )
    return name, email


def _list_workspace_objects(
    api_url: str, db: requests.Session, path: str
) -> Iterator[dict[str, Any]]:
    """Yield every object directly under `path`, following pagination."""
    page_token: str | None = None
    while True:
        params: dict[str, str] = {"path": path}
        if page_token:
            params["page_token"] = page_token
        r = db.get(f"{api_url}/api/2.0/workspace/list", params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        body = r.json()
        yield from body.get("objects", [])
        page_token = body.get("next_page_token")
        if not page_token:
            return


def _stream_export(
    api_url: str, db: requests.Session, ws_path: str, fmt: str, dest_file: Path
) -> None:
    """Stream one workspace object to dest_file.

    Uses direct_download=true so the response body is the raw file, not a
    base64-in-JSON blob, and writes it to disk in chunks so a large object never
    sits fully in memory. Writes to a `.part` file and renames on success, so a
    dropped connection never leaves something that looks like a finished file.

    Raises requests.HTTPError immediately (the caller checks whether that is the
    10 MB export limit); retries a connection that drops mid-stream a few times,
    then raises RuntimeError.
    """
    part = dest_file.with_name(dest_file.name + ".part")
    last_exc: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            with db.get(
                f"{api_url}/api/2.0/workspace/export",
                params={"path": ws_path, "format": fmt, "direct_download": "true"},
                timeout=HTTP_TIMEOUT,
                stream=True,
            ) as r:
                r.raise_for_status()
                with part.open("wb") as fh:
                    for chunk in r.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                        fh.write(chunk)
            part.rename(dest_file)
            return
        except requests.HTTPError:
            part.unlink(missing_ok=True)
            raise
        except requests.RequestException as exc:
            part.unlink(missing_ok=True)
            last_exc = exc
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(2 * attempt)
    raise RuntimeError(
        f"Download of {ws_path!r} failed after {DOWNLOAD_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc


def _is_export_size_error(err: requests.HTTPError) -> bool:
    """True if an export failed because the object exceeds the ~10 MB API limit."""
    resp = err.response
    if resp is None:
        return False
    if resp.status_code == 413:
        return True
    return resp.status_code == 400 and "MAX_NOTEBOOK_SIZE_EXCEEDED" in resp.text


def _copy_file(src: Path, dest_file: Path) -> bool:
    """Copy src to dest_file in chunks. Returns False if src cannot be read."""
    try:
        with src.open("rb") as rf, dest_file.open("wb") as wf:
            while chunk := rf.read(DOWNLOAD_CHUNK_BYTES):
                wf.write(chunk)
        return True
    except OSError:
        return False


def _verify_size(dest_file: Path, expected: int | None, ws_path: str) -> None:
    """Fail if the downloaded file is not the size Databricks reported for it.

    Catches a truncated body or a short read from the /Workspace mount before the
    push, while the source is still available. `expected` is None for objects the
    listing gave no size for (notebooks), and the check is skipped.
    """
    if expected is None:
        return
    actual = dest_file.stat().st_size
    if actual != expected:
        raise RuntimeError(
            f"Downloaded {ws_path!r} is {actual} bytes but Databricks reported {expected}. "
            "The copy is incomplete - re-run; if it persists, copy the file out by hand."
        )


def _export_notebook(api_url: str, db: requests.Session, ws_path: str, dest_file: Path) -> None:
    """Export one notebook as source. Stops the run if it is over the 10 MB limit."""
    try:
        _stream_export(api_url, db, ws_path, "SOURCE", dest_file)
    except requests.HTTPError as err:
        if _is_export_size_error(err):
            raise RuntimeError(
                f"Notebook {ws_path!r} exceeds the 10 MB workspace export limit and "
                "cannot be archived. Clear its cell outputs or split it into smaller "
                "notebooks, then re-run."
            ) from err
        raise


def _export_file(
    api_url: str,
    db: requests.Session,
    ws_path: str,
    dest_file: Path,
    expected_size: int | None,
) -> str | None:
    """Export one non-notebook file.

    Returns None for a normal export, or a one-line note for ARCHIVE_NOTES.md
    when the file was too big for the API and had to be read from the /Workspace
    mount. Stops the run if neither route works, or if the copy is short.
    """
    try:
        _stream_export(api_url, db, ws_path, "AUTO", dest_file)
        _verify_size(dest_file, expected_size, ws_path)
        return None
    except requests.HTTPError as err:
        if not _is_export_size_error(err):
            raise
        dest_file.unlink(missing_ok=True)
        fuse_src = WORKSPACE_FUSE_ROOT / ws_path.lstrip("/")
        if _copy_file(fuse_src, dest_file):
            _verify_size(dest_file, expected_size, ws_path)
            return (
                f"`{ws_path}` exceeded the 10 MB export API limit; archived by reading it "
                f"directly from the {WORKSPACE_FUSE_ROOT} filesystem mount."
            )
        raise RuntimeError(
            f"File {ws_path!r} exceeds the 10 MB workspace export limit and could not be "
            f"read from {WORKSPACE_FUSE_ROOT} (the mount is not available on this "
            "compute). Copy it to a Unity Catalog volume or cloud storage and add it to "
            "the archive project by hand."
        ) from err


def _preserve_empty_dirs(root: Path) -> int:
    """Drop a .gitkeep in every directory whose whole subtree has no files.

    git does not track empty directories; without this the archived folder
    structure silently loses every empty sub-folder on push. One bottom-up pass:
    a directory "has content" if it holds a file directly or a child directory
    that has content, so a single .gitkeep at the bottom of an empty branch keeps
    every directory above it. Returns the number of .gitkeep files created.
    """
    created = 0
    has_content: set[Path] = set()
    dirs = sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for d in dirs:
        if any(e.is_file() or e in has_content for e in d.iterdir()):
            has_content.add(d)
        else:
            (d / GITKEEP_FILENAME).touch()
            has_content.add(d)  # it now holds a file, so its parent is kept too
            created += 1
    return created


def export_workspace_folder(
    api_url: str,
    db: requests.Session,
    ws_path: str,
    dest: Path,
    exclude: set[str],
    report: ArchiveReport,
) -> int:
    """Recursively export ws_path into dest. Returns the number of files written.

    Objects whose workspace path is in `exclude` are skipped - this is how the
    archiver keeps itself out of the archive. Skipped and unusually-handled
    objects are recorded on `report`.
    """
    count = 0

    def recurse(path: str, local_dir: Path) -> None:
        nonlocal count
        local_dir.mkdir(parents=True, exist_ok=True)
        for obj in _list_workspace_objects(api_url, db, path):
            p = obj["path"]
            if p in exclude:
                print(f"  skip     {p}  (excluded)")
                continue
            name = PurePosixPath(p).name
            kind = obj["object_type"]
            if kind == "DIRECTORY":
                recurse(p, local_dir / name)
            elif kind == "NOTEBOOK":
                ext = EXT_BY_LANGUAGE.get(obj.get("language", "PYTHON"), ".py")
                _export_notebook(api_url, db, p, local_dir / f"{name}{ext}")
                count += 1
                print(f"  notebook {name}{ext}")
            elif kind == "FILE":
                note = _export_file(api_url, db, p, local_dir / name, obj.get("size"))
                if note is not None:
                    report.deviations.append(note)
                    print(f"  file     {name}  (via {WORKSPACE_FUSE_ROOT} mount)")
                else:
                    print(f"  file     {name}")
                count += 1
            else:
                report.skipped_objects.append(f"`{p}` ({kind})")
                print(f"  skip     {p}  ({kind}, unsupported)")

    recurse(ws_path, dest)
    report.empty_dirs_preserved = _preserve_empty_dirs(dest)
    report.exported_file_count = count
    return count


def _candidate_project_names(base: str, today: str) -> Iterator[str]:
    """`base_<today>`, then `base_<today>_2`, `base_<today>_3`, ... for same-day re-runs.

    `today` is a UTC `YYYY-MM-DD` string. The date is the normal suffix - it
    says when the snapshot was taken - and the trailing counter only appears
    when the same folder is archived more than once in one day.
    """
    stem = f"{base}_{today}"
    yield stem
    for n in range(2, MAX_SAME_DAY_ARCHIVES + 1):
        yield f"{stem}_{n}"


def _sanitize_project_name(name: str) -> str:
    """Strip the characters GitLab will not accept in a project name.

    Only the offending characters go - the rest of the folder name is kept
    verbatim (case, spaces, accented letters, `.`, `_`, `+`, `-`). Leading
    characters that are not a letter, digit or `_` are dropped (GitLab requires
    the name to start with one of those), as is a trailing `.`.

    Returns "" when nothing usable is left (the name was all punctuation, or a
    script GitLab cannot turn into a project path); the caller turns that into a
    "rename the folder" error.
    """
    kept = [c for c in name if c.isalnum() or c in PROJECT_NAME_EXTRA_CHARS]
    cleaned = "".join(kept).strip()
    while cleaned and not (cleaned[0].isalnum() or cleaned[0] == "_"):
        cleaned = cleaned[1:].lstrip()
    return cleaned.rstrip(". ")


def _create_project(gl: requests.Session, name: str) -> requests.Response:
    return gl.post(
        f"{GITLAB_URL}/api/v4/projects",
        json={
            "name": name,
            "namespace_id": ARCHIVE_GROUP_ID,
            "visibility": "private",
            "initialize_with_readme": False,
        },
        timeout=HTTP_TIMEOUT,
    )


def create_gitlab_project(
    gl: requests.Session, base_name: str, report: ArchiveReport, today: str | None = None
) -> tuple[dict[str, Any], str]:
    """Create a private project in ARCHIVE_GROUP_ID and return (project, name it got).

    Each archive is named `<base_name>_<UTC date>` (e.g. `proj_2026-08-31`), so
    re-archiving a folder never collides with an earlier day's archive and
    nothing needs deleting first. A second run on the same day walks
    `..._2`, `..._3`, ... until a create succeeds - the create call is the
    authority on whether a name is free, so a race just moves to the next one.

    The folder name is used as-is. Only if GitLab rejects it as an invalid
    project name (a 400 that is not "already taken") is it cleaned - the rejected
    characters stripped out, see `_sanitize_project_name` - and tried once more,
    with a note left on `report.deviations`.
    """
    today = today or _utc_today()
    name_base = base_name
    cleaned_already = False
    while True:
        for name in _candidate_project_names(name_base, today):
            r = _create_project(gl, name)
            if r.status_code == 201:
                if cleaned_already:
                    report.deviations.append(
                        f"GitLab does not accept {base_name!r} as a project name; this "
                        f"archive is named `{name}` (disallowed characters stripped). The "
                        "source folder is unchanged."
                    )
                return cast("dict[str, Any]", r.json()), name
            if r.status_code == 400 and "has already been taken" in r.text:
                continue
            if r.status_code == 400 and not cleaned_already:
                break  # invalid name - fall through to the cleaned-name attempt
            raise RuntimeError(f"GitLab project creation failed: {r.status_code} {r.text}")
        else:
            raise RuntimeError(
                f"{base_name!r} has already been archived {MAX_SAME_DAY_ARCHIVES} times "
                f"today in group {ARCHIVE_GROUP_ID} ({name_base}_{today} .. "
                f"_{MAX_SAME_DAY_ARCHIVES}). Check whether the run is looping, or wait "
                "until tomorrow."
            )

        cleaned = _sanitize_project_name(base_name)
        if not cleaned or cleaned == base_name:
            raise RuntimeError(
                f"GitLab rejected {base_name!r} as a project name and stripping the "
                "characters it disallows leaves nothing usable. Rename the workspace "
                "folder to start with a letter or digit and re-run."
            )
        name_base = cleaned
        cleaned_already = True


def _authenticated_url(http_repo_url: str, token: str) -> str:
    """Push URL with the token embedded - passed as a git arg, never stored or logged."""
    parts = urlsplit(http_repo_url)
    netloc = f"oauth2:{quote(token, safe='')}@{parts.netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _git_runner(repo_dir: Path, token: str) -> Callable[[list[str]], None]:
    """A `git -C repo_dir ...` caller that scrubs the token from any error text."""

    def git(args: list[str]) -> None:
        res = subprocess.run(
            ["git", "-C", str(repo_dir), *args], capture_output=True, text=True, check=False
        )
        if res.returncode != 0:
            detail = (res.stderr or res.stdout).strip().replace(token, "***")
            raise RuntimeError(f"git {' '.join(args).replace(token, '***')}\n{detail}")

    return git


def _commit_identity(name: str, email: str) -> list[str]:
    return ["-c", f"user.name={name}", "-c", f"user.email={email}", "-c", "commit.gpgsign=false"]


def push_to_gitlab(repo_dir: Path, http_repo_url: str, token: str, name: str, email: str) -> None:
    """git init + commit + push the export to main. The token never touches disk or logs."""
    git = _git_runner(repo_dir, token)
    ident = _commit_identity(name, email)
    git(["init", "-q", "-b", "main"])
    git([*ident, "add", "-A"])
    git([*ident, "commit", "-q", "-m", COMMIT_MESSAGE])
    git([*ident, "push", "-q", _authenticated_url(http_repo_url, token), "main:main"])


def commit_notes_file(
    repo_dir: Path, http_repo_url: str, token: str, name: str, email: str
) -> bool:
    """Commit and push ARCHIVE_NOTES.md as a second commit. Returns False if the push fails.

    A failure here does not fail the run - the archive content is already pushed
    and verified at this point; only the record file did not land.
    """
    git = _git_runner(repo_dir, token)
    ident = _commit_identity(name, email)
    try:
        git([*ident, "add", NOTES_FILENAME])
        git([*ident, "commit", "-q", "-m", NOTES_COMMIT_MESSAGE])
        git([*ident, "push", "-q", _authenticated_url(http_repo_url, token), "main:main"])
        return True
    except RuntimeError as exc:
        print(f"  WARNING: could not push {NOTES_FILENAME}: {exc}")
        return False


def _remote_blob_paths(gl: requests.Session, project_id: int) -> set[str]:
    """Every file path on the project's default branch (paginated tree walk)."""
    paths: set[str] = set()
    url: str | None = f"{GITLAB_URL}/api/v4/projects/{project_id}/repository/tree"
    params: dict[str, Any] | None = {
        "recursive": "true",
        "per_page": 100,
        "pagination": "keyset",
    }
    while url:
        r = gl.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        paths.update(i["path"] for i in r.json() if i["type"] == "blob")
        url = r.links.get("next", {}).get("url")
        params = None  # the "next" link already carries the query string
    return paths


def validate_push(
    gl: requests.Session, project_id: int, repo_dir: Path, report: ArchiveReport
) -> None:
    """Compare exported files to what is on the remote. Records the result on `report`.

    GitLab's tree API can briefly lag a push, so a non-empty "missing" set is
    re-checked a few times before it is believed.
    """
    local = {
        p.relative_to(repo_dir).as_posix()
        for p in repo_dir.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(repo_dir).parts
    }
    missing: list[str] = []
    for attempt in range(1, TREE_RECHECK_ATTEMPTS + 1):
        missing = sorted(local - _remote_blob_paths(gl, project_id))
        if not missing or attempt == TREE_RECHECK_ATTEMPTS:
            break
        print(f"  {len(missing)} file(s) not on remote yet; re-checking...")
        time.sleep(2 * attempt)

    report.missing_on_remote = missing
    keep = report.empty_dirs_preserved
    breakdown = f" ({len(local) - keep} archived + {keep} .gitkeep placeholder(s))" if keep else ""
    if missing:
        report.verification = (
            f"{len(missing)} of {len(local)} file(s){breakdown} MISSING on remote"
        )
        print(f"  WARNING: {len(missing)} file(s) missing on remote: {missing[:10]}")
    else:
        report.verification = f"all {len(local)} files present on remote{breakdown}"
        print(f"  verified: all {len(local)} files present on remote")


def set_project_description(gl: requests.Session, project_id: int, report: ArchiveReport) -> None:
    """Set the GitLab project description so its state is visible on the project page.

    Three levels: a plain archive, one where something was handled specially, or
    one where a file failed to verify.
    """
    if report.is_incomplete:
        description = f"INCOMPLETE ARCHIVE - not every file verified. See {NOTES_FILENAME}."
    elif report.deviations:
        description = (
            f"Databricks workspace archive - something handled specially, see {NOTES_FILENAME}."
        )
    else:
        description = f"Databricks workspace archive. See {NOTES_FILENAME}."
    try:
        r = gl.put(
            f"{GITLAB_URL}/api/v4/projects/{project_id}",
            json={"description": description},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"  WARNING: could not set the project description: {exc}")


def confirm_notes_on_remote(gl: requests.Session, project_id: int) -> bool:
    """True if ARCHIVE_NOTES.md is visible on the project's main branch."""
    try:
        r = gl.get(
            f"{GITLAB_URL}/api/v4/projects/{project_id}/repository/files/"
            f"{quote(NOTES_FILENAME, safe='')}",
            params={"ref": "main"},
            timeout=HTTP_TIMEOUT,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


def render_archive_notes(report: ArchiveReport) -> str:
    """Render ARCHIVE_NOTES.md from a finished run's report."""
    out: list[str] = []
    add = out.append

    add("# Archive notes")
    add("")
    add(
        f"This GitLab project is a point-in-time copy of a Databricks workspace folder, made "
        f"by the `{TOOL_NAME}` tool ({TOOL_LOCATION}). The original workspace folder was not "
        f"changed."
    )
    add("")

    if report.is_incomplete:
        add("## Status: INCOMPLETE")
        add("")
        add(
            f"**{len(report.missing_on_remote)} exported file(s) did not verify on the remote** "
            "(listed below). Do not treat this project as a complete copy of the source folder."
        )
    else:
        add("## Status: COMPLETE")
        add("")
        add("**Every exported file was verified present on the remote.**")
    add("")
    add(f"- Files archived: {report.exported_file_count}")
    add(f"- Empty sub-folders preserved with `{GITKEEP_FILENAME}`: {report.empty_dirs_preserved}")
    add(f"- Remote verification: {report.verification}")
    if report.missing_on_remote:
        add("")
        add("### Files missing on the remote")
        add("")
        out.extend(f"- `{p}`" for p in report.missing_on_remote)
    add("")

    add("## Where this came from")
    add("")
    add("| Field | Value |")
    add("|---|---|")
    add(f"| Source workspace folder | `{report.source_folder}` |")
    if report.project_name:
        add(f"| Archive project | `{report.project_name}` |")
    add(f"| Databricks workspace | {report.workspace_url} |")
    add(f"| Archived by | {report.author} |")
    add(f"| Run started (UTC) | {report.run_started_utc} |")
    add("")

    if report.deviations:
        add("## Handled specially")
        add("")
        out.extend(f"- {d}" for d in report.deviations)
        add("")

    if report.skipped_objects:
        add("## Workspace objects not archived")
        add("")
        add("These existed in the source folder but this tool cannot export them:")
        add("")
        out.extend(f"- {s}" for s in report.skipped_objects)
        add("")

    add("## What this archive does NOT contain")
    add("")
    add("This is a snapshot of notebooks and files only. It does not include:")
    add("")
    add("- Notebook revision history - each file is a single current version")
    add("- Jobs, job schedules, cluster or SQL-warehouse configuration")
    add("- Permissions / ACLs on the folder or its contents")
    add("- Secrets or secret scopes")
    add("- Data: DBFS, Unity Catalog volumes and tables, MLflow experiments and models")
    add("- Anything outside the archived folder")
    add("")

    add("## Restoring this archive")
    add("")
    add(
        "1. In Databricks, add this GitLab project as a Git folder: **Repos -> Add Repo**, "
        "and paste the project URL."
    )
    add(
        f"2. `{GITKEEP_FILENAME}` files just mark folders that were empty in the source (git "
        "cannot store an empty folder). They have no effect on anything - leave them or delete "
        "them, whichever you prefer."
    )
    if report.deviations:
        add(
            "3. Review **Handled specially** above - something in this archive is not in its "
            "original workspace form."
        )
    add("")
    return "\n".join(out)


def archive_workspace(dbutils: Any) -> None:
    ctx = get_databricks_context(dbutils)
    api_url = ctx["api_url"]
    db = _session({"Authorization": f"Bearer {ctx['api_token']}"})

    name, email = get_user_details(api_url, db)
    source = str(PurePosixPath(ctx["notebook_path"]).parent)
    base_name = PurePosixPath(source).name
    today = _utc_today()
    exclude = {ctx["notebook_path"]}

    report = ArchiveReport(
        source_folder=source,
        workspace_url=ctx["workspace_url"],
        author=f"{name} <{email}>",
        run_started_utc=_utc_now(),
    )

    print(f"Source folder : {source}")
    print(f"Excluding     : {ctx['notebook_path']}  (this file)")
    print(f"GitLab project: {base_name}_{today}  (group {ARCHIVE_GROUP_ID})")
    print(f"Commit author : {name} <{email}>")

    with tempfile.TemporaryDirectory(prefix="ws-archive-") as tmp:
        repo_dir = Path(tmp) / base_name
        count = export_workspace_folder(api_url, db, source, repo_dir, exclude, report)
        if count == 0:
            raise RuntimeError("Nothing to archive - the folder has only this file, or is empty.")
        print(f"Exported {count} files")

        token = get_gitlab_token(dbutils)
        gl = _session({"PRIVATE-TOKEN": token})
        project, project_name = create_gitlab_project(gl, base_name, report, today=today)
        report.project_name = project_name
        if project_name != f"{base_name}_{today}":
            print(f"  project name: {project_name!r}  (folder already archived today, or cleaned)")
        print(f"Created {project['web_url']}")

        push_to_gitlab(repo_dir, project["http_url_to_repo"], token, name, email)
        print("Pushed to main")
        validate_push(gl, project["id"], repo_dir, report)

        (repo_dir / NOTES_FILENAME).write_text(render_archive_notes(report), encoding="utf-8")
        if commit_notes_file(repo_dir, project["http_url_to_repo"], token, name, email):
            if confirm_notes_on_remote(gl, project["id"]):
                print(f"Wrote {NOTES_FILENAME}")
            else:
                print(f"  WARNING: {NOTES_FILENAME} was pushed but is not visible on the remote")
        set_project_description(gl, project["id"], report)

    status = "INCOMPLETE - see ARCHIVE_NOTES.md" if report.is_incomplete else "OK"
    print(
        f"\nDone ({status}).\n  GitLab: {project['web_url']}\n"
        f"  Source: {source}  (unchanged)\n"
        "  To open this in Databricks: Repos > Add Repo, and paste the GitLab URL above."
    )


# `dbutils` is a global the Databricks runtime injects into every notebook. In
# Databricks the name is present and this runs the archive; imported anywhere
# else (a test, a linter) it is absent and nothing executes.
if "dbutils" in globals():
    archive_workspace(dbutils)  # noqa: F821
