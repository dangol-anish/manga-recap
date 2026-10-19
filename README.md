# Manga Recap — Stage 1

Local FastAPI backend, plain HTML/JavaScript Library, and JSON files on disk. All source was built inside this repository; model code and weights come from the official public Magi v3 source.

## Run

```sh
./run.sh
```

Open http://127.0.0.1:8000. Stop the server with Ctrl+C.

**Stage 1 is approved. Only the registered manga’s Chapter 1 and Chapter 12 are authorized for processing.** The API, Library controls and worker enforce this chapter restriction. All other chapters remain disabled.

## Library

Put manga inside this repo, for example:

```
manga/MyManga/Chapter001/001.png
manga/MyManga/Chapter001/002.png
```

Enter `manga/MyManga` and select right-to-left or left-to-right reading order. Folder paths outside this repo are rejected. Adding manga does not start processing. No bulk manga run is available in this stage.

The background worker processes one page at a time. It writes JSON atomically, skips existing valid page outputs, and preserves completed results across a stop. Interrupted jobs appear paused after a server restart. Existing invalid JSON is flagged and does not count as complete. One server and one background chapter worker run at a time.

## Detection and validity

Official model: https://huggingface.co/ragavsachdeva/magiv3

Pinned revision: `49c73a225122d53adbaa26d53868be81a57706e2`.

Use Magi v3's normal detection prompt and unconstrained decoding with the official three-beam settings. Parse its labeled output and retain only boxes with the exact `<panel>` label. All other detections are immediately discarded and never persisted, drawn, or used. There is no forced panel-only decoding. The combined upstream detection-and-association helper is not called: only its detection generation step is used, with no OCR, character grounding, speaker guesses, or association pass.

The official Magi geometric sorter orders retained panels. For LTR, coordinates are mirrored for sorting only; output coordinates stay in the original image space. A page is valid only when:

- It has at least one panel.
- Every box has finite coordinates and lies inside the original page bounds with positive width and height.
- Every box covers at least 1% of the original page area.

A failure flags the entire page, rather than silently removing, clipping, or relabeling a box. Flagged pages are included in the report and produce no valid page JSON. Every processed page receives a red-box preview; existing valid JSONs are reused and any missing preview is regenerated from its saved panel boxes. The same gate is applied when saving, resuming, and counting Library progress.

Valid output: `outputs/<manga-id>/<chapter>/<page-name>.json`. Only panel coordinates are saved, as `[x1, y1, x2, y2]`, with one-based reading order. Model identity, original page dimensions, reading direction, device and timing are also recorded.

Apple Silicon uses MPS float16; other machines use CPU float32. Batch size is one and CPU threads are limited to two. The model has approximately 833 million parameters and 1.7 GB of weights. The model card permits personal and non-commercial use; see its license for other usage.

## Approved chapter processing

`app/worker.py` saves red panel boxes numbered in reading order for every page in each approved chapter. It runs `caffeinate -i -w <worker-pid>` on macOS to prevent idle sleep while processing. The assertion ends when that worker finishes or stops; no persistent power setting is changed.

Chapter reports live in `outputs/<manga-id>/<chapter>/_report.json`; page previews live in the same chapter’s `previews/` folder. Library shows valid page progress, flagged pages, current page and whether the Mac is kept awake. Only one chapter runs at a time. The fixed controller `scripts/run_approved_chapters.py` processes Chapter 12 then Chapter 1 through the live Library API and saves `reports/approved-chapters.json`.

The per-chapter average uses saved detection times for all evaluated pages, including cached valid pages and flagged pages. Total time measures wall time for the current run, including model loading and preview saving; reused pages are listed separately, so their prior inference time is not counted again in current wall time. Total panels counts panels on valid pages. Flagged results remain listed separately.

## Previous six-page review

The fixed-scope test script is `scripts/test_chapter12.py`. It reads only Chapter 12 pages `001.png` through `006.png` from the registered manga, generates normal detections, saves valid panel-only JSON, and saves red panel boxes numbered in reading order under the chapter's `previews/` directory. It cannot expand its page range through command-line arguments. The original benchmark keeps processing disabled; the subsequently approved two-chapter run uses the separate controller above.

Report: `reports/chapter12-retest.json`. Its `pages` list records time and retained panel count per page, and its `flagged` list records any sanity-check failures. Page times include image loading, model inference, filtering and ordering; they exclude preview rendering and JSON writing. Model loading and total wall time are reported separately. The previous failed experiment is documented in `reports/chapter12-validation.json`; its mixed-category coordinate files were removed.

## Fresh setup

Use Python 3.11 inside this repo:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install --no-cache-dir -r requirements.lock.txt
.venv/bin/python scripts/install_model.py
```

Model files and caches stay inside this repo. `.venv/`, `.cache/`, `vendor/`, manga images, previews, outputs, library state and `.env` are excluded from git.

Tests: `.venv/bin/python -m unittest discover -s tests -v`.

## Stage 2 — Review panels

Stage 2 reads saved panel JSON only for Chapter 1 and Chapter 12. No model inference is run. Use each chapter's **Review panels** link in Library. The contact sheet shows every whole page with large IDs, places flagged pages first, and lists every flag reason.

Each panel crop is named `p003-1.png`, `p003-2.png`, and so on, in reading order. Padding is 1% of that panel's width/height on each side, clamped to original page edges, with integer pixel edges rounded outward. Crops, labeled whole pages and thumbnails are stored under `outputs/<manga-id>/<chapter>/stage2/<page-id>/<revision>/`. The adjacent `page.json` identifies the active assets and stores Stage 2 fixes; Stage 1 JSONs remain unchanged.

In **Edit panels**, use the up/down buttons to reorder, select a box and drag a blue corner (or edit pixel coordinates) to resize, delete the selected box, or choose **Draw box** and drag on the page. **Save fixes & regenerate** saves the current reading order and regenerates only that page's crops/labeled image. A concurrent edit from another tab is rejected instead of overwriting newer fixes. Old crop sets are retired after a complete new set is committed.

Pages are flagged if a box covers at least 90% of the page with other boxes, two boxes actually intersect and the intersection width exceeds 5% of page width **and** its height exceeds 5% of page height, a box's width/height is under 10% of the respective page dimension, a box's area is under 3%, or the page is a spread. The overlap rule requires both dimensions to exceed 5%; touching boxes, shared edges and thin strips are not flagged. Zero-panel pages receive a whole-page panel and a flag. Flags are recalculated after each save; they are review prompts and do not prevent cropping or saving valid in-bounds geometry.

Reports: each chapter's `stage2/_report.json`; initial combined summary and flagged reasons in `reports/stage2-panels.json`. Chapter reports update after fixes. Initial preparation is `.venv/bin/python scripts/prepare_stage2.py`; existing Stage 2 fixes are preserved when preparation is rerun.

## Revised Stage 3 — Continuous story beats (Chapter 1 only)

Open Chapter 1's **Export & import narration** link in Library. Existing labeled-page ZIPs stay unchanged: pages 001–020 (80 available IDs), and pages 021–037 (70 available IDs). No new detection, cropping or labeling is run.

Download the selected batch's fresh prompt. It contains the updated master, that batch's exact valid-ID manifest, the saved cast, the previous narration's last three beats, and the preceding imported continuity summary/open_threads. Batch 2's prompt becomes available after importing Batch 1. A chapter or batch ending does not trigger an ending to the story. Only the explicit **final batch of my entire recap** checkbox permits a grounded closing; leave it off for ordinary uploads. Select it before downloading the prompt.

Reply format:

```json
{
  "cast": [],
  "beats": [
    {"panel_ids": ["p003-1", "p003-2"], "narration": "A connected story beat.", "unsure": ""}
  ],
  "continuity": {"summary": "Factual context to carry forward.", "open_threads": []}
}
```

Example IDs are illustrative, not an allowed-ID list. Selected IDs must actually occur in the batch manifest, cannot repeat, and must follow reading order within/across beats. Skipped IDs are allowed and counted in the UI. At least one beat and one selected ID are required. The narration budget scales at 40 words per selected ID in a beat; a two-panel beat allows 80 words. Legacy panel-by-panel replies are rejected with a format explanation.

The prompt forbids invented IDs, story events, dialogue, thoughts, motives, relationships and names, and asks for omission or uncertainty when evidence is unclear. Structural validation enforces IDs, ordering and schema; it cannot establish factual accuracy or natural writing. Those still need human review of a real AI reply.

On failure, the exact offending IDs/beat numbers and a copyable redo request appear. Partial corrections identify the affected current draft beat with `replace_beat`; unaffected beats are retained. To drop an affected beat, return its replace_beat with an empty panel_ids array and empty narration. Cast/continuity-only corrections use an empty beats array. The combined result is revalidated before any saved story data changes. You can uncheck **Corrections only** to submit a full replacement reply instead.

Successful imports atomically save beats, cast, continuity and the final-recap choice in `data/manga/<manga-id>/narration.json`. Chapter beats are assembled in batch order. Changing earlier narration makes dependent batch replies stale, so they must be regenerated with fresh continuity. Previous context never comes from a future batch. Imported badges show beat/selected/skipped counts.

`reports/stage3-beats-test.json` records controlled structural tests against Batch 1's actual 80-ID manifest, performed in temporary storage inside this repo. No test narration is saved to the actual manga. The `scripts/test_stage3_batch1.py` test never reads/processes images or another chapter. Per-batch generated prompts are under `outputs/<manga-id>/Chapter 1/stage3/prompts/<batch-id>/narration_prompt.md`.

Stage 4: Chapter 1 narration audio
--------------------------------
From Chapter 1's narration page, choose “Listen & generate audio”. Generate missing audio, listen to individual beats or in sequence, and regenerate any individual beat. Uses Edge TTS 7.3.1 with the English (US) Andrew Multilingual narrator at normal speed, using FFmpeg and lightweight PCM trimming to shorten detected quiet gaps longer than 200 ms to approximately 100 ms. Internet is required: narration text goes to Microsoft's online speech service. Generated MP3s and their JSON manifest stay under the chapter's ignored stage3/audio directory. No images, cast metadata, or other chapters are sent. Completed clips are reused on retry; changed narration requires fresh audio. Empty narration remains silent. Run with `./run.sh`.

Stage 5: Chapter 1 video
-----------------------
From the audio page choose “Preview & export video”. Each beat initially divides its decoded audio duration equally among its selected panel crops. Play from a selected beat, scrub the audio, edit the switch times in seconds from that beat's start, and save. Switches must increase and allow at least one frame per panel. Reset a beat to equal timing and save to undo manual timing. Silent narration beats, if present, default to two seconds.

Render MP4 produces a local 1280×720, 24 fps H.264/AAC video with simple cuts and black borders where needed, using installed FFmpeg/ffprobe with two encoding threads. Download it or watch the finished MP4 on the page. Cuts are quantized to video frames. Decoded audio sample durations prevent accumulated MP3 padding gaps. Changing timing, audio revisions, narration or panel revisions makes the export outdated. Only current versions can be downloaded. Stage 5 outputs and timing JSON stay under the ignored Chapter 1/stage5 directory. Existing source crops and narration audio are not modified. Run with `./run.sh`.
