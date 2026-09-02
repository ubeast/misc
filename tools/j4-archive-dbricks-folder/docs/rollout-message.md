# Rollout message — new archive tool

Draft announcement to send to the developer group when the tool is ready for
group testing. Fill in `[secret details]` and `[DATE]` before sending.

---

**Subject: New archive tool — please test this week**

Team,

New Databricks -> GitLab archive tool is ready to replace the previous one.
**Don't switch over yet** — I need the group to test it first.

**Repo + instructions:** https://github.com/ubeast/misc — `tools/j4-archive-dbricks-folder/`

**Setup:** GitLab PAT (`api` + `write_repository`) stored as a Databricks
secret; set `GITLAB_SECRET_SCOPE` / `GITLAB_SECRET_KEY` in the file. `[secret details]`

**Run:** drop `tools/j4-archive-dbricks-folder/j4_archive_dbricks_folder.py` into a folder, Run all.
Ends with `Done (OK)`.

**Please test:**

- Archive several different folders (big, nested, mixed notebooks / files / empty sub-folders)
- Re-archive the same folder — same day (-> `_2`) and a later day (-> new dated project)
- A folder with an awkward name (leading `.`, `&`, `!`)
- Confirm the source folder is untouched, and the GitLab project has everything + `ARCHIVE_NOTES.md` + 2 commits

**Report:** thumbs up here if clean, or file an issue with the details.

Once we get the all-clear I'll delete the test archives and it's yours to use.
Please finish by `[DATE]`.

Thanks,
Michael
