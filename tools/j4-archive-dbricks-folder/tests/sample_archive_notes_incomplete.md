# Archive notes

This GitLab project is a point-in-time copy of a Databricks workspace folder, made by the `j4_archive_dbricks_folder` tool (the ubeast/misc repo, tools/j4-archive-dbricks-folder/). The original workspace folder was not changed.

## Status: INCOMPLETE

**1 exported file(s) did not verify on the remote** (listed below). Do not treat this project as a complete copy of the source folder.

- Files archived: 38
- Empty sub-folders preserved with `.gitkeep`: 3
- Remote verification: 1 of 42 file(s) (38 archived + 3 .gitkeep placeholder(s)) MISSING on remote

### Files missing on the remote

- `reference/historical_rates_2019.parquet`

## Where this came from

| Field | Value |
|---|---|
| Source workspace folder | `/Users/dev@example.gov/R&D pricing model` |
| Archive project | `RD pricing model_2026-08-28` |
| Databricks workspace | https://example-prod.cloud.databricks.com |
| Archived by | Dev Example <dev@example.gov> |
| Run started (UTC) | 2026-08-28 16:05:41Z |

## Handled specially

- GitLab does not accept 'R&D pricing model' as a project name; this archive is named `RD pricing model_2026-08-28` (disallowed characters stripped). The source folder is unchanged.
- `/Users/dev@example.gov/R&D pricing model/reference/rate_table.parquet` exceeded the 10 MB export API limit; archived by reading it directly from the /Workspace filesystem mount.

## Workspace objects not archived

These existed in the source folder but this tool cannot export them:

- `/Users/dev@example.gov/R&D pricing model/tuning-runs` (MLFLOW_EXPERIMENT)

## What this archive does NOT contain

This is a snapshot of notebooks and files only. It does not include:

- Notebook revision history - each file is a single current version
- Jobs, job schedules, cluster or SQL-warehouse configuration
- Permissions / ACLs on the folder or its contents
- Secrets or secret scopes
- Data: DBFS, Unity Catalog volumes and tables, MLflow experiments and models
- Anything outside the archived folder

## Restoring this archive

1. In Databricks, add this GitLab project as a Git folder: **Repos -> Add Repo**, and paste the project URL.
2. `.gitkeep` files just mark folders that were empty in the source (git cannot store an empty folder). They have no effect on anything - leave them or delete them, whichever you prefer.
3. Review **Handled specially** above - something in this archive is not in its original workspace form.
