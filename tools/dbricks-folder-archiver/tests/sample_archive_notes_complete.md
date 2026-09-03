# Archive notes

This GitLab project is a point-in-time copy of a Databricks workspace folder, made by the `dbricks_folder_archiver` tool (the ubeast/one-file-tools repo, tools/dbricks-folder-archiver/). The original workspace folder was not changed.

## Status: COMPLETE

**Every exported file was verified present on the remote.**

- Files archived: 15
- Empty sub-folders preserved with `.gitkeep`: 1
- Remote verification: all 16 files present on remote (15 archived + 1 .gitkeep placeholder(s))

## Where this came from

| Field | Value |
|---|---|
| Source workspace folder | `/Users/dev@example.gov/customer-churn` |
| Archive project | `customer-churn_2026-08-28` |
| Databricks workspace | https://example-prod.cloud.databricks.com |
| Archived by | Dev Example <dev@example.gov> |
| Run started (UTC) | 2026-08-28 15:22:04Z |

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
