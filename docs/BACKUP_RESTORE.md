# Backup, restore and storage

Open **Backup & storage** in the sidebar. This is a utility page alongside the existing stages; it does not change the production workflow. Viewing sizes and backup names is read-only. All work stays inside the current cloned repository and requires no paid tool.

## Create a backup

1. Stop other processing and avoid editing local files while backing up.
2. Press **Create backup** and keep the app running.
3. Watch the file steps: archive writing, source consistency checks, and reading every saved archive entry to verify its SHA-256.
4. After completion, use **Open backups folder**. Copy the finished ZIP to another drive for protection against drive failure.

The backup includes eligible files under `manga/`, `data/`, and `outputs/`: original images, hidden download/renumbering markers, imported narration/context, manual panel fixes, job state, crops, audio, videos and prior revisions. API keys / `.env` files, models, installed packages, caches, runtime locks/stop signals, maintenance bookkeeping and diagnostic logs are excluded. It uses ZIP64 and stores media without compression to reduce CPU use. A SHA-256 manifest is included. Partial/unverified archives are not shown as completed backups; graceful stopping removes the task's partial archive. Backups are full snapshots, not incremental backups.

A backup that detects source changes fails without publishing a completed archive. Current files are not modified. The tool checks free disk space before writing; backups need roughly the total eligible file size again plus working space. Backups are under ignored `backups/`, outside the source folders, so they do not recursively include themselves or go to GitHub.

## Restore a backup

1. Install the app and required models in the target clone. Supply `.env` keys separately.
2. Copy the app-created backup ZIP into that clone's `backups/` folder using Finder/Explorer.
3. Choose the backup and press **Check restore first**.
4. Review the exact list of missing, identical and differing files.
5. Default restore adds missing files and reuses identical files. Any differing file blocks the entire restore. No differing files are overwritten by default.
6. Only if you want the backup versions, explicitly enable **Restore backup versions of differing files**, run the preview again, and review it. Each replaced current file is copied and verified under `backups/restore-safety/<task-id>/` before replacement.
7. Tick the review confirmation and press **Restore these files**. Avoid editing files in other programs during restore.

Replacement restores complete files, including saved narration/progress JSON; it does not merge chapter records inside JSON. An older snapshot can therefore return the app's saved state to an earlier version. Current replaced files remain in the safety copy. Files that are absent from the archive are not removed. Restoring into a fresh clone is the clearest way to inspect an older snapshot independently.

Before changing the workspace, restore stages and verifies every archived file, rejects unsafe paths, symlinks, duplicate names, unexpected entries and invalid hashes, and checks space for the staged archive plus safety copies. It then publishes files with a durable per-file journal. New files use atomic no-overwrite hard links on the supported local APFS/NTFS drives. Stop/failure rolls back files published by that task. Startup rolls back an interrupted restore rather than automatically continuing it. Outside file changes are retained; unresolved recovery blocks processing and offers **Check restore recovery**. Keep the named safety folder when reporting a recovery error.

A stopped/failed storage task does not automatically resume. Create a new backup or run a fresh restore/cleanup preview before trying again. Historical successful backups and safety copies are never automatically deleted.

## Safe cleanup

1. Press **Preview cleanup**.
2. Read the exact list and file-byte total. Nothing has been deleted yet.
3. Only if you agree, tick the permanent-deletion confirmation and press **Delete listed caches**.

The whitelist is deliberately narrow:

- Installer downloads under `.cache/pip/`.
- Derived PNG composition frames named by a 64-character hash in `outputs/<manga-id>/<chapter>/narration/video-stage7/frames/`. The video code can rebuild these from preserved crops.

Cleanup never selects original manga, narration, prepared panels/crops, generated audio/videos, old media revisions, models, backups or restore safety copies. Unknown files and older revisions are kept rather than guessed to be disposable. Changes since preview invalidate cleanup before deletion; each selected file is checked again before removal. A partial cleanup can be followed by another preview. Deleted caches cannot be undone, but can be recreated by the installer/video workflow. Models and existing finished media remain usable.

Sizes count logical file bytes, including duplicate readable copies and old revisions, rather than exact allocated filesystem blocks. Symbolic links are skipped for storage totals and rejected for backup/restore/cleanup input. Model caches often use symlinks; their real blob files are counted without following links. The original-image breakdown shows manga input folders; generated media is included in the separate generated-files total.

## Boundaries and recovery

- Use one app server and a writable local APFS/NTFS clone. Do not use a network share or exFAT for restore publication.
- Other heavy tasks and app writes are blocked during storage operations. Read-only playback/status remains available. Avoid external editors during backup/restore.
- The tool accepts its own manifest-based ZIP format, not arbitrary ZIP/CBZ manga archives. Current limits: 100,000 files and a 32 MB manifest. Larger workspaces need a manual copy of the complete `manga`, `data`, `outputs` directories instead.
- A Mac backup with filenames Windows cannot represent is rejected with a file-specific error. Preserve the backup and use a separate careful migration; do not casually rename folders associated with saved narration.
- Do not copy the Mac virtual environment to Windows. Recreate dependencies and models using the Windows setup guide.
- A backup stored on the same drive is a recovery snapshot, not protection against that drive failing. Keep another copy elsewhere.
- No real backup, restore or cleanup is run during implementation or by app startup. Tests use isolated files and injected failures.
