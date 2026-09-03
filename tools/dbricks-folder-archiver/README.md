# dbricks-folder-archiver

Archive the Databricks workspace folder this notebook lives in to a **new private
GitLab project**. The original workspace folder is never modified.

One file: [`dbricks_folder_archiver.py`](dbricks_folder_archiver.py). Unlike
the other tools here it is **not** a plain script and not standard-library only —
it is a **Databricks notebook** (the file's first line is
`# Databricks notebook source`) and it uses `requests` (pre-installed on
Databricks Runtime) and `git`. It runs only inside Databricks: it needs the
`dbutils` runtime global and a notebook execution context to find the folder it
is running from.

Each run creates one new project in the GitLab group set by `ARCHIVE_GROUP_ID`,
named `<folder>_<UTC date>` — e.g. `sales-model_2026-08-31`. Re-archiving the
same folder on another day just makes another dated project; a second run on the
*same* day adds a `_2`, `_3`, … counter. Nothing is overwritten and no earlier
archive needs deleting.

If GitLab won't accept the folder name as a project name (a leading `.`, an `&`,
`#`, `!` and the like), the characters it rejects are stripped out — for the
project name only. The source folder is never touched, and the full original
name is recorded in `ARCHIVE_NOTES.md`.

## What it does

Run it from inside the folder you want to archive. It:

| # | Step | Detail |
|---|------|--------|
| 1 | **Export** | Recursively exports every notebook, file and sub-folder — **except this notebook itself** — to a temp directory. Empty sub-folders get a `.gitkeep`. Large-file handling is in [Limitations](#limitations). |
| 2 | **Create** | Creates a private GitLab project named `<folder>_<UTC date>` in the group set by `ARCHIVE_GROUP_ID`. Same-day repeats get a `_2`, `_3`, … counter; different days never collide. Characters GitLab rejects in a project name are stripped from the name. |
| 3 | **Push** | Pushes the export as a single commit on `main`, authored by whoever ran it. |
| 4 | **Verify** | Confirms every exported file is present on the remote (paginated tree walk, with a few re-checks so a lagging tree API doesn't flag a good archive). |
| 5 | **Record** | Writes `ARCHIVE_NOTES.md` into the project as a second commit: where it came from, verification result, anything skipped or handled unusually, what the archive does **not** contain, and restore steps. Sets the GitLab project description to match — plain, "handled specially", or "INCOMPLETE". |

To browse the archive inside Databricks afterwards, add it as a Git folder by
hand: **Repos → Add Repo**, and paste the project URL the run prints.

## Prerequisites

- A Databricks workspace where you can run notebooks or jobs.
- A GitLab personal access token with `api` + `write_repository` scope, stored as
  a Databricks secret — scope and key set by `GITLAB_SECRET_SCOPE` /
  `GITLAB_SECRET_KEY` (see [Configuration](#configuration)).
- Permission to create projects in the target group (`ARCHIVE_GROUP_ID`).
- No cluster libraries needed — only `requests` (pre-installed on Databricks
  Runtime) and `git`.

## How to run

1. Get `dbricks_folder_archiver.py` into the Databricks workspace folder you
   want to archive — copy it in, import it, or create the file there and paste
   the contents. Keep the `# Databricks notebook source` first line so Databricks
   treats it as a notebook.
2. Open it and **Run all**, or point a job notebook-task at it.

The bottom of the file calls `archive_workspace(dbutils)` when the Databricks
`dbutils` global is present, so running it runs the archive. (Outside Databricks
the guard is false and nothing happens — that is what lets the tests import it.)

## Configuration

All constants are in the `CONFIG` block at the top of the file:

| Constant | Purpose |
|---|---|
| `ARCHIVE_GROUP_ID` | GitLab group ID that new archive projects are created in. Change this one value to target a different group — GitLab: open the group → "…" menu → "Copy group ID". |
| `GITLAB_URL` | GitLab base URL. |
| `GITLAB_SECRET_SCOPE` / `GITLAB_SECRET_KEY` | Databricks secret scope and key where the PAT is stored. |
| `HTTP_TIMEOUT` | Per-request timeout, seconds. |
| `COMMIT_MESSAGE` / `NOTES_COMMIT_MESSAGE` | Messages for the archive commit and the `ARCHIVE_NOTES.md` commit. |
| `AUTHOR_NAME_OVERRIDE` / `AUTHOR_EMAIL_OVERRIDE` | Leave both blank to attribute the commit to whoever runs the script (resolved from the Databricks SCIM `Me` endpoint). Set both to attribute it to someone else. |

> **Note:** the committed `GITLAB_URL` / `GITLAB_SECRET_SCOPE` / `GITLAB_SECRET_KEY`
> / `ARCHIVE_GROUP_ID` values are one developer's dev values, not a shared
> configuration. Set them for your environment before a real run.

## Output

- A private GitLab project at `<GITLAB_URL>/…/<folder-name>_<date>`, with a
  one-line description saying whether the archive is plain, has files handled
  specially, or is incomplete.
- `ARCHIVE_NOTES.md` at the root of that project — read it first when restoring.
  It records where the archive came from (including the dated project name),
  whether every file verified, what was skipped, and what a snapshot like this
  does not include.
- The source folder, unchanged.

Empty sub-folders in the archive contain a `.gitkeep` (git cannot track an empty
directory). Delete them on restore if you want the folders genuinely empty.

[`tests/sample_archive_notes_complete.md`](tests/sample_archive_notes_complete.md)
and [`tests/sample_archive_notes_incomplete.md`](tests/sample_archive_notes_incomplete.md)
are full worked examples of what `ARCHIVE_NOTES.md` looks like — one clean run,
one with a missing file, a mount fallback, and a skipped object.

## Limitations

- **10 MB export limit.** Databricks caps `workspace/export` at 10 MB regardless
  of format. A non-notebook file over that is read directly from the `/Workspace`
  mount instead — which works on interactive clusters and recent job clusters,
  but not everywhere; if that read fails the run stops and names the file. A
  **notebook** over 10 MB cannot be exported at all: the run stops and tells you
  to clear its cell outputs or split it. Every such event is written into
  `ARCHIVE_NOTES.md`.
- A re-archive is a **new project** (`<folder>_<date>`), not an update of the
  last one — there is no diffing or incremental push. Each archive is a full,
  independent snapshot. The run stops only if the same folder is archived more
  than 99 times in one day (a runaway loop).
- **Folder names GitLab won't accept.** The folder name is used as the project
  name as-is; only if GitLab rejects it are the disallowed characters stripped
  out (`A&B analysis` → `AB analysis`, `.scratch` → `scratch`), and a note is
  left in `ARCHIVE_NOTES.md`. Two folders that strip to the same name don't
  overwrite — the second becomes `..._<date>_2`. A name with **no** usable
  characters left stops the run with a message to rename the folder.
- The tool does **not** create a Databricks Repos checkout of the archive. Add
  one by hand if you want it (**Repos → Add Repo**, paste the project URL).
- The record commit (step 5) is best-effort. If it fails to push, the archive
  content is still complete and verified; only `ARCHIVE_NOTES.md` is missing, and
  the run says so.
- Files export with `format=AUTO`; unusual binary types may not round-trip
  perfectly. Each file's size is checked against the size Databricks reported for
  it, so a truncated copy stops the run — but content is not otherwise compared.

## Tests

```bash
uv run pytest tools/dbricks-folder-archiver        # or: pytest tools/dbricks-folder-archiver
```

The network, `dbutils`, and GitLab are all faked; real `git` runs against a local
bare repo. Includes one end-to-end pass through `archive_workspace`. The
committed `tests/sample_archive_notes_*.md` golden files are checked against the
renderer by `test_render_matches_committed_sample`; after a deliberate wording
change regenerate them with
`python tools/dbricks-folder-archiver/tests/regenerate_samples.py`.

`requests` is pinned in the repo's dev dependency group so `uv run pytest` has it.

## Acceptance test

Run this once by hand in the real environment after any change to the tool —
there is no automation that can. It confirms the manual placement steps and the
live Databricks and GitLab APIs behave as the unit tests assume.

1. Ensure the GitLab PAT secret exists (`GITLAB_SECRET_SCOPE` / `GITLAB_SECRET_KEY`).
2. In a Databricks workspace, create a throwaway folder containing: two notebooks
   (one Python, one SQL), one small file, one sub-folder with a notebook in it,
   and one empty sub-folder.
3. Copy `dbricks_folder_archiver.py` into that folder (keep the
   `# Databricks notebook source` first line). **Run all.** The summary should
   end `Done (OK)`.
4. In GitLab, open the new project and confirm: every notebook and file is
   present with the folder structure intact; the empty sub-folder holds only
   `.gitkeep`; `ARCHIVE_NOTES.md` is at the root and reads correctly; there are
   **two commits** on `main`; the archiver notebook itself is **not** there.
5. Confirm the original folder is unchanged.
6. **Run all again** without deleting anything. Because it is the same day, the
   summary should note the run is `<folder>_<date>_2` and end `Done (OK)`.
7. Repeat once from a folder whose name GitLab rejects — e.g. `.R&D test!` —
   and confirm the run reaches `Done (OK)`, the project is named `RD test_<date>`,
   and its `ARCHIVE_NOTES.md` has a **Handled specially** note naming the original
   folder.
8. Delete the test projects and the test folder.
