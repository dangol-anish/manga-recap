# Code map

The current app runs at `127.0.0.1:8000`, stores local JSON and uses one heavy job at a time. This document maps existing code; it does not introduce a new workflow or storage layout.

## Current workflow

| UI / route | Backend | Browser code |
| --- | --- | --- |
| Downloader `/` | `downloader.py`, `download_sources.py`, `download_requests.py` | `downloader.html/js` |
| Renumbering `/renumbering` | `renumbering.py` | `renumbering.html/js` |
| Detection `/detection` | `detection.py`, `detection_worker.py`, `detector.py` | `detection.html/js` |
| Panel Review `/panels` | `panel_preparation.py` | `panel_preparation.html/js`, panel editor |
| Narration `/story` | `narration_stage5.py`, shared `narration.py` validation | `story.html/js`, `story_preview.js` |
| Audio `/speech` | `grouped_audio.py`, `speech_stage6.py` | `speech.html`, `grouped_audio.js`, `speech.js` |
| Video `/cinema` | `video_stage7.py`, shared `video.py` compositor | `cinema.html/js` |
| Stitch `/stitch` | `stitch.py` | `stitch.html/js` |
| Shorts `/shorts` | `shorts.py`, `shorts_render.py`, `shorts_review.py` | `shorts.html/js`, `shorts_review.js` |
| Backup & storage `/storage` | `maintenance.py` | `storage.html/js` |

Python filenames in this table are under `app/`; browser files are under `app/static/`. Page styles use the corresponding CSS plus shared sidebar/controls styles.

## Shared responsibilities

- `main.py`: app lifespan, route registration, local-request protection and historical compatibility routes. Recovery discovers interrupted work; it must not automatically restart generation.
- `storage.py`: repository-relative paths and atomic JSON writes. Input files remain separate from outputs.
- `tasks.py`: sequential heavy-task exclusion across stages, including backup/recovery safety.
- `errors.py` and `static/errors.js`: actionable, redacted diagnostics and browser transport/media handling. `request_error` centralizes the six current workflow modules' identical 409/400 mapping without changing historical Library 403/404 behavior.
- `read_cache.py`: request-scoped read snapshots and file-hash caching. Writes must validate fresh state.
- `batch_work.py`: explicit multi-chapter audio/video queues; each chapter keeps its own output.
- `memory.py`: preceding-source lineage and bounded context. `story_memory.py`: separate user pins/verification/resolution overlays; no script rewrite.
- `audio_quality.py`: pronunciation review data and local signal/timing checks; never creates speech.
- `grouped_audio.py` also owns key selection, cooldowns and quotas. Secrets stay in ignored `.env`; never log values.
- `forced_timing.py`, `alignment.py`, `audio.py`: speech timing, shared alignment and audio utilities. Their historical callers still matter.
- `audio_folders.py`, `video_folders.py`, `media_layout.py`: readable safe media copies, not the authoritative internal job assets.
- `workflow.py` and `static/workflow.js`: explicit read-only chapter progress checks.

## Source and saved-work boundaries

Download → numbered images → detections → prepared panel revisions → imported narration → saved audio/timestamps → timing/MP4. Each stage binds its output to source versions and rejects stale work. Earlier files and media revisions remain on disk; they are not erased when a newer result becomes current.

Narration is manually generated in an external AI chat. Gemini long-form audio is grouped across chapters, but publishes compatible chapter/beat timing for Video. Shorts snapshots selected narration and source panel references separately; it synthesizes its own selected scripts and never reads long-form MP4s. Stitch reads finished readable chapter videos and copies compatible streams without re-encoding.

`manga/`, `data/`, `outputs/`, `backups/`, `.env` and models are local-only. [Workflow](WORKFLOW.md) and [Backup & restore](BACKUP_RESTORE.md) describe their user-facing behavior. Cleanup of source code must not migrate or delete any of these directories.

## Compatibility code is intentional

`/library`, older narration/audio/video routes, and modules such as `review.py`, `narration.py`, `audio.py`, `alignment.py` and `video.py` still serve saved work or are shared by current stages. Older stage numbers refer to an earlier workflow. Do not delete or rename those modules/routes merely because the sidebar now uses different numbers. Historical scripts under `scripts/` remain explicit commands, never automatic startup work.

## Verification

See [Development](DEVELOPMENT.md) for the single cross-platform test command. Regression fixtures exercise real local media plus mocked providers/models; they do not prove live source-site availability, provider quotas, narration accuracy or Windows model performance. Native platform CI remains the place to check each platform's current result after an authorized push.
