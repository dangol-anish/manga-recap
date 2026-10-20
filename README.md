# Manga Recap

A local FastAPI app that turns chapter images into narrated recap videos. Use any registered manga and chapter; no Codex session is needed to operate the app. Python, plain HTML/JavaScript, JSON on disk, free local tools. An external image-capable AI writes narration through manual export/import. Edge TTS sends narration to Microsoft's online service and needs internet; detection, editing and rendering run locally.

## Run

From this repository:

```sh
./run.sh
```

On Windows, run `run.bat` from Command Prompt or `.\run.bat` from PowerShell after creating `.venv` and installing `requirements.txt`. The Windows virtual environment uses `.venv\Scripts\python.exe`; macOS uses `.venv/bin/python`.

Open <http://127.0.0.1:8000/>. Dependencies live in this repo's `.venv`; FFmpeg and ffprobe must be available. To install from scratch, create `.venv`, install `requirements.txt`, and run `scripts/install_model.py` with that environment. The downloader installs the pinned official `ragavsachdeva/magiv3` model, revision `49c73a225122d53adbaa26d53868be81a57706e2`, into `vendor/magiv3`. Detection also requires a successful model-validation record; the existing installation already has one. Historical smoke/validation scripts remain in `scripts/`.

## Portable local storage

Downloads always live inside **your clone**, at `manga/<manga>/<chapter>/001.jpg` (or PNG/WebP), regardless of where you cloned it or your operating system. The app resolves this location from its own source files, not the terminal working directory. Saved job paths are relative, so moving the whole repository with its local data keeps downloads resumable. `manga/`, `data/`, `outputs/` and `.venv` are ignored by Git: pushing source code does not upload local manga, job state or media. A fresh clone starts without those local files and creates download folders when needed.

## Stage 1 Downloader

The home page is now **Stage 1: Downloader**, using the approved dark glass palette. The existing pipeline is preserved at `/library`. Paste a MangaPill or WeebCentral **series URL**, load its chapters, select individual chapters or a range of list positions, and click **Download selected**. Only the selected images download; no panel detection is started.

The app discovers selected page lists before showing determinate page progress. The bar counts verified images, with per-chapter totals and exact failed-page reasons. Up to three images transfer concurrently, with three attempts each. Images retain their actual PNG/JPEG/WebP format and are named `001`, `002`, etc. under source-specific manga and chapter folders inside `manga/`.

**Stop download** preserves completed images. **Recent downloads → Resume / retry** re-fetches the current image lists, verifies saved hashes, skips intact files, and retries incomplete/corrupt pages. Restarted server jobs become paused and resume only when requested. A changed source page order stops that chapter rather than silently reusing mismatched pages. Temporary files and atomic JSON records protect interrupted writes; chapter ownership prevents jobs from writing over each other.

Source adapters are independent Python implementations in this repo. They use HTTPX and the standard HTML parser, without a browser engine or paid service. Current public-site access can change; blocked/changed source responses are reported explicitly. No PDF/CBZ conversion.

## Daily workflow

1. Put manga inside this repo: `manga/MyManga/<chapter folders>/<page images>`. Natural filename sorting supports numbered images and names such as `page_001.jpg`. Keep a chapter's files stable after importing its narration. Add the manga in Library and select right-to-left or left-to-right reading direction.
2. Click **Process chapter**. Detection runs in a background worker, one page at a time. It skips valid existing JSONs and keeps the Mac awake. Stop/resume preserves completed results. Only panel detections are retained; text/character detections are discarded. No OCR or character associations run.
3. Click **Prepare panels**, then **Review panels**. Preparation preserves existing manual fixes. Review significant geometry/order mistakes. Crop padding, labeled copies and review flags follow the project rules. Failed detector pages may become flagged whole-page panels for review; invalid detections never become valid Stage 1 JSONs.
4. Click **Export & import narration**. For each batch (at most 20 pages), download the ZIP and its current prompt. Extract the ZIP. **Start a fresh AI chat**, attach those labeled images and send the entire prompt together. Do not rely on any previous chat.
5. Paste the AI JSON into the selected batch and click **Validate & import**. The receipt identifies the manga, chapter, batch, page revisions and prior context. IDs may repeat in another chapter, but a receipt cannot be reused there. Errors offer targeted corrections without changing a valid saved import. New replies include selected story beats, exact phrase anchors, cast, continuity, a chapter summary and durable memory updates. Imports made with the older format remain readable.
6. Open **Audio** and generate speech. New manga default to Andrew at 1.2× with brief 180 ms period pauses; existing saved pacing stays intact. New imports with phrase anchors capture actual Edge TTS word timestamps and obtain phrase-aligned cuts automatically. Empty narration stays silent. Listen and regenerate individual beats if needed. Existing clips survive failed regeneration.
7. Open **Video**. Preview and adjust switch times, save and render an MP4. Existing imported narration without anchors can use equal/manual timing or the phrase-map editor. Applying saved maps explicitly generates timestamped speech and rebuilds that chapter. The sharp unstretched panel, blurred background, shadow, smooth zooms/slides and short crossfades are shared across chapters.

No chapters are processed automatically. One heavy detection, speech, alignment or video job runs at a time across Library. Folder discovery does not authorize a background run.

## Story context without an endless chat

The app stores memory per manga, with separate original imports for each chapter and batch. Narration begins with whichever chapter you import first. Subsequent participating chapters follow the natural chapter-folder order; earlier included chapters must have valid imports. Merely downloading more chapter folders does not enroll them into the story.

Each batch prompt carries only **preceding valid history**:

- Imported cast available before that batch.
- Relevant confirmed memory facts and separately labeled uncertain claims, with chapter/batch/panel provenance.
- Original recent chapter summaries and short excerpts of older chapter summaries.
- The latest continuity/open threads and the previous narration ending.
- The exact current panel manifest and a batch receipt.

The context packet is capped at **18,000 characters** (the narration master and current ID manifest are separate). It selects facts by relevance to the recent story and recency, bounds cast and summaries, and reports omitted counts. It does not repeatedly rewrite one giant summary. Full original summaries, memory and previous import revisions remain on disk even when omitted from a prompt. This is bounded context, not a guarantee that every distant detail reaches the AI. Factual correctness still requires human review.

Open **Story memory** from a manga's Library card to search all saved chapter history, review uncertainty, and correct summaries, facts or cast. Imported facts explicitly marked uncertain stay separate; facts supported by panels with an `unsure` annotation are conservatively treated as uncertain. A human can correct their status in memory review. The code cannot prove the truth of an AI's prose.

Edits to earlier narration, panels or memory invalidate dependent later imports. Saved later narration/media remain on disk, but the app blocks using stale imports until replies are refreshed against current context. New manga never inherit another manga's cast or memory. Later chapters never leak into prompts for earlier chapters.

## Local storage and compatibility

- `data/library.json`: registered manga and reading directions.
- `data/manga/<id>/narration.json`: canonical imports, chapter summaries, sourced memory and retained earlier revisions; atomically saved with cast/continuity.
- `outputs/<id>/<chapter>/`: detector JSONs/previews, Stage 2 crops/labeled pages, Stage 3 exports/audio and Stage 5 timings/video/alignment.
- Existing Chapter 1 assets remain at their original paths and keep their original hashes. Python helper defaults and its old MP4 basename remain for compatibility; web requests always pass the selected manga/chapter explicitly.

Manga, local state, generated media, model weights, caches, `.env` and virtual environments are ignored by Git. Small source files and JSON verification reports can be committed. Nothing reads or imports another project on this computer.

## Validation

```sh
.venv/bin/python -m unittest discover -s tests -q
```

Tests use isolated synthetic manga inside this repository and mocked speech/model responses. They include real FFmpeg exports, multi-manga/chapter isolation, 80-chapter bounded context, stale dependency rejection, fresh-chat receipts, uncertainty, memory editing/search, filename ordering and imported phrase anchors. They do not process real additional manga or call online speech services.
