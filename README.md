# Manga Recap

A local FastAPI app that turns chapter images into narrated recap videos. Use any registered manga and chapter; no Codex session is needed to operate the app. Python, plain HTML/JavaScript, JSON on disk, free local tools. An external image-capable AI writes narration through manual export/import. Edge TTS sends narration to Microsoft's online service and needs internet; detection, editing and rendering run locally.

## Run

From this repository:

```sh
./run.sh
```

On Windows, run `run.bat` from Command Prompt or `.\run.bat` from PowerShell after creating `.venv` and installing `requirements.txt`. The Windows virtual environment uses `.venv\Scripts\python.exe`; macOS uses `.venv/bin/python`.

Open <http://127.0.0.1:8000/>. Dependencies live in this repo's `.venv`; FFmpeg and ffprobe must be available. To install from scratch, create `.venv` and install `requirements.txt`. Then install and explicitly validate the pinned official `ragavsachdeva/magiv3` model, revision `49c73a225122d53adbaa26d53868be81a57706e2`:

```sh
# macOS, from the repository root
.venv/bin/python -m scripts.install_model
.venv/bin/python -m scripts.validate_model
```

On Windows, use `.venv\Scripts\python.exe -m scripts.install_model` followed by `.venv\Scripts\python.exe -m scripts.validate_model`. Validation runs normal detection on a synthetic image, checks panel filtering and geometry, and records the validated model revision in ignored `data/model-validation.json`. It never reads or processes manga chapters. A failed check does not overwrite an existing record. Stage 3 stays disabled until validation succeeds; opening the app never runs this check automatically. Model files remain in ignored `vendor/magiv3`.

## Create audio or videos for several chapters

On **Audio** or **Video**, use **Create audio/videos for several chapters**. Choose a manga, check the ready chapters, then press **Create selected audio/videos**. The app works through them in chapter order, one at a time, and shows progress for each chapter. Finished current work is reused; audio generation also joins the chapter's clips into its full track. Each chapter keeps its own files.

You can close the browser tab while the local app stays running. **Stop selected chapters** saves completed work, and **Resume unfinished chapters** retries only unfinished chapters. After restarting the app, resumption is always explicit. If narration, panels or timing changed, start a new selection for that chapter. Queue state is stored under ignored `data/batch-work/`.

## Find saved audio and videos

Use **Open saved audio folder** on Audio or **Open saved videos folder** on Video. These buttons are always visible and open Finder on macOS or Explorer on Windows. Files are organized in `outputs/Audio/<manga name>/` and `outputs/Videos/<manga name>/`. Files use names such as `Chapter1-Audio.mp3`, `Chapter1-Beat001-Audio.mp3` and `Chapter1-Video.mp4`, with no chapter subfolders. Existing readable copies are migrated on use; old folder trees remain safely backed up under ignored `outputs/.media-folder-backups/`. Opening the folder prepares readable copies of saved media; it never generates new speech or video. Existing files and older versions are retained.

## Portable local storage

Downloads always live inside **your clone**, at `manga/<manga>/<chapter>/001.jpg` (or PNG/WebP), regardless of where you cloned it or your operating system. The app resolves this location from its own source files, not the terminal working directory. Saved job paths are relative, so moving the whole repository with its local data keeps downloads resumable. `manga/`, `data/`, `outputs/` and `.venv` are ignored by Git: pushing source code does not upload local manga, job state or media. A fresh clone starts without those local files and creates download folders when needed.

## Stage 1 Downloader

The home page is now **Stage 1: Downloader**, using the approved dark glass palette. The existing pipeline is preserved at `/library`. Paste a MangaPill or WeebCentral **series URL**, load its chapters, select individual chapters or a range of list positions, and click **Download selected**. Only the selected images download; no panel detection is started.

The app discovers selected page lists before showing determinate page progress. The bar counts verified images, with per-chapter totals and exact failed-page reasons. Up to three images transfer concurrently, with three attempts each. Images retain their actual PNG/JPEG/WebP format and are named `001`, `002`, etc. under source-specific manga folders inside `manga/`, with consistent chapter names such as `Chapter 1` and `Chapter 2.5`. Chapter folders have no generated ID suffix. Conflicting releases are rejected instead of merged or renamed with a suffix.

**Stop download** preserves completed images. **Recent downloads → Resume / retry** re-fetches the current image lists, verifies saved hashes, skips intact files, and retries incomplete/corrupt pages. Restarted server jobs become paused and resume only when requested. A changed source page order stops that chapter rather than silently reusing mismatched pages. Temporary files and atomic JSON records protect interrupted writes; chapter ownership prevents jobs from writing over each other.

Source adapters are independent Python implementations in this repo. They use HTTPX and the standard HTML parser, without a browser engine or paid service. Current public-site access can change; blocked/changed source responses are reported explicitly. No PDF/CBZ conversion.

## Stage 2 Renumbering

Open **Stage 2: Renumbering** in the sidebar after manually deleting unwanted images in Finder or File Explorer. Refresh folders, select chapter folders from `manga/`, and click **Renumber selected chapters**. There is no preview step. Each completed chapter has a right-aligned **Open folder** button for Finder or File Explorer. Each selected chapter starts at `0000`, using natural filename order; original extensions (including their case) and image bytes are preserved. No images are deleted or processed by detection.

Renumbering uses temporary staging and exclusive hard links instead of an overwriting rename. This works on standard local APFS/NTFS filesystems. Filesystem support failures preserve the images and report the problem. A JSON journal in `data/renumbering/` and a hidden chapter marker allow interrupted operations to continue: refresh, select the affected chapter and press **Renumber selected chapters** again. Do not manually change chapter files while renumbering is in progress. Collisions and stale previews stop safely instead of overwriting files.

A cleaned/renumbered download folder is protected against Downloader resume so it cannot restore the unwanted images you deleted. Existing download history and original manifests remain saved. Renumbering is always started explicitly; adding the page does not rename real chapters automatically.

## Stage 3 Panel Detection

Open **Stage 3: Panel Detection**. Choose a manga, reading direction, and chapter folders; press **Start detection**. This is an explicit action: opening the page never processes chapters. A separate Python process loads the existing pinned Magi v3 model once, processes one page at a time, and keeps the Mac awake. The app permits one heavy task at a time, including detection, download, speech and rendering. Stage 3 uses the current Python interpreter on macOS/Windows, without a hardcoded venv executable path.

Normal model detection runs; non-panel outputs are discarded immediately. No OCR or character association runs. Each valid page gets ordered panel JSON and a red-box numbered preview under `outputs/<manga-id>/<chapter>/detection/`. `report.json` holds per-chapter totals, page times and flagged reasons. Invalid boxes never enter the valid `pages/` directory. Unreadable source images are flagged without a preview. The page shows overall and per-chapter progress, elapsed time, valid/flagged counts, panel counts, average seconds per page, expandable previews (flagged first), and native output-folder buttons.

**Stop detection** finishes the current page before pausing. **Resume detection** verifies source SHA-256, model revision, normal-detection mode and reading direction. Only matching valid results and unchanged flagged reports are reused. Missing valid previews are recreated without model inference. Changed inputs/settings are reprocessed, with older generated JSON/preview revisions archived. Original manga images and the older pipeline outputs are preserved. On server shutdown completed checkpoints remain safe; a surviving worker is stopped and its lock prevents concurrent model workers. Job state/logs live under `data/detection/` and are ignored by Git.

Magi v3 must already be installed and its panel-filtering validation record must exist. Model startup is shown separately because it can take time; cached-only runs do not load the model. Windows has no POSIX alarm, so a 20-second ordering watchdog stops only the isolated worker if official sorting hangs; previous page checkpoints remain safe. Tests exercise source replacement, settings/model invalidation, flags, stop/resume, isolated worker startup, a real cached subprocess run, previews, native folder actions and cross-manga isolation using synthetic images and a fake detector. Real manga detection is started only by the user; Windows model inference has not been tested on a Windows machine.

## Stage 4 Panel Review

Open **Stage 4: Panel Review**. Choose a manga and chapters with completed, current Stage 3 detections, then press **Prepare selected chapters**. Preparation runs in the background one page at a time with progress and Stop/Resume. It does not load Magi or run detection/OCR. Opening this page never prepares chapters automatically.

Each panel gets a unique chapter-local ID such as `p0000-1`, `p0000-2`, `p0001-1`. Numeric page stems are preserved with at least four digits; mixed/non-numeric filenames use zero-based natural page order. IDs follow the panel order. Crops get 1% of the panel width/height as padding per edge, clamped to the page. Full-page labeled PNGs, thumbnails, crop PNGs, independent page manifests and a chapter report live under `outputs/<manga-id>/<chapter>/panels/`. All remain outside Git. Original manga and Stage 3 detections are unchanged.

Press **Review panels** to open the chapter contact sheet, with flagged pages first and reasons shown. **Edit panels** opens a source-page canvas: select and drag corners to resize; draw a box; delete a selected box; use arrows to reorder; or enter exact coordinates. **Save fixes** commits a new asset revision for that page only. Existing source hashes and revisions are checked before saving. Manual fixes are preserved on preparation resume; missing assets can be rebuilt from their saved boxes. Changed sources/detections for edited pages are reported as conflicts instead of silently discarding fixes. Old committed versions remain available on disk for preservation; active manifests point to one current version.

Flags retain the existing review rules: a full-page-like box (at least 90%) with other boxes; intersections exceeding 5% of page width **and** 5% of page height; boxes under 10% of page width/height or 3% of page area; landscape spreads; and zero-panel fallbacks. Stage 3 flagged pages without valid boxes become flagged whole-page placeholders for manual correction. Deleting all boxes likewise creates a flagged whole-page panel. An unreadable source image cannot be cropped and is reported as a preparation error.

Use **Open folder** in the review heading to inspect the chapter output in Finder/File Explorer. No later narration/audio/video stage is started. Tests use isolated synthetic detection outputs and check pixel content, ID order, crop padding, flags, optimistic save conflicts, rollback on image-save failure, source immutability, resumable jobs and real editor control handlers.

## Daily workflow

1. Put manga inside this repo: `manga/MyManga/<chapter folders>/<page images>`. Natural filename sorting supports numbered images and names such as `page_001.jpg`. Keep a chapter's files stable after importing its narration. Add the manga in Library and select right-to-left or left-to-right reading direction.
2. Click **Process chapter**. Detection runs in a background worker, one page at a time. It skips valid existing JSONs and keeps the Mac awake. Stop/resume preserves completed results. Only panel detections are retained; text/character detections are discarded. No OCR or character associations run.
3. Click **Prepare panels**, then **Review panels**. Preparation preserves existing manual fixes. Review significant geometry/order mistakes. Crop padding, labeled copies and review flags follow the project rules. Failed detector pages may become flagged whole-page panels for review; invalid detections never become valid Stage 1 JSONs.
4. Click **Export & import narration**. For each batch (at most 20 pages), download the ZIP and its current prompt. Extract the ZIP. **Start a fresh AI chat**, attach those labeled images and send the entire prompt together. Do not rely on any previous chat.
5. Paste the AI JSON into the selected batch and click **Validate & import**. The receipt identifies the manga, chapter, batch, page revisions and prior context. IDs may repeat in another chapter, but a receipt cannot be reused there. Errors offer targeted corrections without changing a valid saved import. New replies include selected story beats, exact phrase anchors, cast, continuity, a chapter summary and durable memory updates. Imports made with the older format remain readable.
6. Open **Audio** and generate speech. New manga default to Brian Multilingual at 1.0× with brief 180 ms period pauses; existing saved pacing stays intact. New imports with phrase anchors capture actual Edge TTS word timestamps and obtain phrase-aligned cuts automatically. Empty narration stays silent. Listen and regenerate individual beats if needed. Existing clips survive failed regeneration.
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

## Stage 5 Narration

Open **Stage 5: Narration**, or **Export & narrate** beside a fully prepared Stage 4 chapter. Choose a manga and chapter. **Export photos** opens a native Finder/Explorer folder with up to 20 labeled PNGs, in page order, and no ZIP. Select all photos in that folder and attach them to a fresh AI chat. Click **Copy batch prompt** and paste it with the images. The prompt is also shown in a selectable text box if clipboard access is unavailable. Paste the JSON reply into the matching batch and click **Validate & import**. Later prompts unlock after earlier imports. Only enable the final recap closing if the entire recap ends here; choose this before copying the prompt.

Prompts use `prompts/narration_master.md` plus exact panel IDs, preceding cast, narration ending and bounded story memory. The JSON must use selected story beats in reading order; panels may be skipped. Each non-silent beat includes exact phrase anchors for its selected panels. Imports check IDs, duplicates/order, 40 words per selected panel, anchors, memory evidence IDs and the batch/context receipt. Validation cannot verify the story against the manga; review the AI's factual claims yourself. Failed drafts leave saved narration untouched and provide a copyable request for targeted corrections.

Successful imports persist beats, cast, continuity, chapter summaries and cited memory together in `data/manga/<manga-id>/story-stage5.json`. Original imports and prior revisions remain saved. Prompts include only valid preceding history, bounded to 18,000 characters of context; future chapters never feed earlier batches. Changes to source panels or earlier narration mark dependent imports stale. Copy updated affected prompts and regenerate replies; do not relabel an old receipt. Historical narration/audio/video are kept separate. Photo export folders and failed drafts stay under `outputs/<manga-id>/<chapter>/narration/`, ignored by Git. No AI service is called by this stage.


## Stage 6 Audio

Open **Stage 6: Audio**, or **Continue to Audio** after importing all Stage 5 batches. Select a fully narrated chapter and press **Generate missing audio**. New chapters default to Brian Multilingual at 1.0× with measured 180 ms period pauses; chapters with existing audio retain their saved voice and pacing; narration text goes to Microsoft's online speech service and requires internet. The script is unchanged. FFmpeg must be installed and available on PATH.

Listen to individual beats or the complete chapter, and regenerate any spoken beat. Generation runs sequentially. Stop waits for the current clip; Resume retries unfinished clips, and restarting the app leaves interrupted jobs paused. A failed generation preserves completed clips and the previous version of a regenerated beat. Silent beats have no audio. Changes to narration, selected panels, phrase anchors or source context invalidate affected clips; the app never serves stale audio as current.

New audio, adjusted real word timestamps, phrase switch timestamps and prior clip revisions live under `outputs/<manga-id>/<chapter>/narration/audio-stage6/`. Jobs live under `data/speech/jobs/`; both are ignored by Git. Historical narration and media are separate. Opening the page never calls TTS or generates a video.

The **Full chapter audio** player provides one seekable track assembled from current beat MP3s in order. Assembly uses local FFmpeg only, without calling TTS or adding inter-beat gaps. Completed generation/regeneration refreshes this track when all beats are ready; **Prepare full audio** assembles already-existing clips. Regenerating a beat invalidates the old combined track. Previous full tracks are retained, and an atomic `chapter.json` records their ordered beat offsets.


## Stage 7 Video

Open **Stage 7: Video**, or **Continue to Video** from Stage 6. Select a chapter with all current audio generated. The preview is a native HTML5 video player playing the actual rendered MP4, with seeking, volume and fullscreen controls. Click **Build video preview** when no current video exists; it uses the same render as MP4 export and the existing Stage 6 audio/phrase timestamps. It uses the approved blurred/dimmed current-panel background, sharp foreground with depth, fractional slow zoom/slide motion and short crossfades. Seeking decodes the MP4 itself; the main player does not swap separate panel images. Status polling preserves playback position.

Select a beat, adjust its panel switch times in seconds relative to the beat, then **Save timing**. Every panel must have at least one frame. **Reset to phrase timing** restores that beat's original speech-aligned defaults; save it to apply. Silent beats have no speech anchors and use a two-second visual hold (longer only if needed for minimum frame counts), divided among their panels. Preview audio includes matching silence. Unsaved timing blocks export; concurrent or stale saves are rejected. After changing timing, save and rebuild the preview before playing the updated version.

Click **Export MP4** for a background local 1280×720 / 24 fps H.264/AAC export, with progress, playback and download when complete. Existing beat audio is decoded into the soundtrack without TTS or script changes; MP4 uses AAC encoding. Inputs and prior exports are preserved. Changed sources/timing invalidate the current export. Render failures retain prior files, and shutdown cancels the export process safely; interrupted jobs require an explicit retry. Opening this page never exports a chapter.

Stage 7 timing, cached composition frames, optional silent-padded preview audio and MP4s live under `outputs/<manga-id>/<chapter>/narration/video-stage7/`. Job records live under `data/cinema/jobs/`. Historical videos remain separate, and generated files stay ignored by Git.


## Detect panels across manga

On **Panel Detection**, choose a manga, check chapters and set its reading direction. Switch to another manga and check more chapters; previous selections stay checked. Review **Selected chapters**, then click **Start detection** once. **Select all in this manga** and **Clear this manga** affect only the current manga; **Clear all selections** resets the entire selection. Detection uses one model and processes pages sequentially, with separate progress, reading directions and output folders for each manga. Stop/resume keeps the original direction settings of the selected job and reuses unchanged saved pages.

## Stage 8 Stitch

Open **Stitch**, choose a manga, check at least two finished chapter videos, then press **Combine videos**. The listed filenames show exactly which saved versions will be joined. They play in natural chapter order. Progress appears directly below the button. Watch the finished MP4, use **Save video to computer**, or **Open folder**. The result is saved as `Combined video.mp4` alongside the manga's chapter videos; later versions receive numbered suffixes.

Stitch uses free local FFmpeg stream copying, preserving the pictures and audio without another encode. Selected videos must have matching video and audio settings. Stop or app shutdown retains the original chapters; **Try again** restarts an interrupted join after checking its sources. Nothing starts automatically on reopening the page. Stitch never generates narration, speech or chapter videos. Job records live under ignored `data/stitch/jobs/`.
