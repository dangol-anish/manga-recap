# Current approved build: experimental Shorts branch

- Build Shorts on `codex/shorts-workflow`, separately from long-form work. Bundle user-selected chapters' current narration JSONs and relevant context for external Gemini chat; provide a copyable prompt and compatible structured-output schema. Import proposals with complete short scripts and chapter-qualified source beat/panel references, including zero proposals. Validate receipts, IDs, source freshness, chronology and word budgets without changing saved work on failure. Provide optional selected-proposal labeled-page visual checks. Generate separate new Brian audio and vertical 30–120 second MP4s with quick phrase cuts, timed captions and smooth motion only on explicit selection. Preserve original narration, panels, audio and videos; save Shorts projects/jobs/revisions under ignored data/outputs. Maintain sequential heavy-task exclusion, stop/retry/recovery, native MP4 playback, dark glass controls and external-browser-only verification. Test only isolated fixtures; never automatically synthesize real shorts. No automatic commit/push.

# Multi-manga panel detection

- Stage 3 allows selecting chapters across multiple manga in one sequential job. Preserve selections when switching manga, show a grouped selection summary, retain each manga's own reading direction, and show per-manga and per-chapter progress. Save direction snapshots with the job and reuse them on resume even if library defaults change. Keep panel-only model filtering, output isolation and existing safe-cache/stop/resume behavior. Never automatically run real chapters during implementation.

# Current approved build: Stage 8 Stitch

- Add Stage 8 Stitch to select multiple finished chapter videos from the readable Videos folders, combine them in natural chapter order with a single explicit button, show progress and a native MP4 player, and save the combined MP4 beside the selected manga videos. Use lossless FFmpeg stream copying for matching video/audio formats; reject incompatible settings clearly. Keep stop/retry, persisted job recovery and existing heavy-task exclusion. Never automatically stitch real chapters during implementation. Preserve source video/audio/script/timing exactly. Migrate existing readable audio and video copies to flat manga folders, including individual beat clips; retain old trees as ignored backups. Keep the dark glass palette, shared controls, external-browser-only rule, and no automatic commit/push.

# Saved media folder buttons

- Audio and Video must each have an always-visible header folder button, without requiring manga/chapter selection. Open clean native Audio/Videos folders organized by readable manga name with files directly inside each manga folder, such as Chapter1-Audio.mp3, Chapter1-Beat001-Audio.mp3 and Chapter1-Video.mp4. No chapter subfolders in these readable media folders. Maintain safe copies; do not expose internal IDs/logs, move original assets, overwrite user changes or regenerate media when opening folders. Keep copies under ignored outputs and mapping indexes under ignored data.

# Multiple-chapter audio and video

- Audio and Video offer explicit chapter selection with Create selected, per-chapter progress, safe stop/resume and persistent queues. Process sequentially to stay lightweight; preserve separate chapter outputs and reuse current completed media. Do not start real generation during implementation or on app startup.

# User-facing wording

- Write for nontechnical users. Use plain action labels such as Create video and Save video to computer; explain where files go and avoid render/export jargon in the interface.

# Current audio default

- New narration audio defaults to Brian Multilingual at 1.0× (+0%) with 180 ms period pauses. Preserve existing chapters' recorded voice, pacing, audio, phrase timestamps and videos when changing defaults; do not regenerate media automatically.

# Current approved build: Stage 7 Video

- The user requested a proper video player because asynchronous image swaps repeated old panels during seeking. Use a native HTML5 video element playing the actual rendered MP4, with native seeking/fullscreen; do not simulate main preview playback with still-image swaps. Build/reuse the current MP4 when explicitly requested; the initial real-chapter preview render for this fix is approved. Timing edits invalidate the preview until rebuilt, and status polling must never reload or rewind the same current MP4. Preserve all existing audio.

- Build a separate generic Stage 7 Video page from current Stage 4 crops, valid Stage 5 narration and existing Stage 6 audio/phrase timestamps. Preserve audio and script exactly; never run TTS, detection or cropping. Default panel switches to saved phrase times, provide synchronized preview and per-beat timing edits/reset, and export MP4 with the approved blurred panel background, depth, fractional smooth zooms/slides and brief crossfades. Keep frame rounding cumulative, reject stale edits/exports, isolate new files from historical videos, retain prior exports, render locally in the background with progress and safe shutdown. Use the dark glass palette and shared controls. Test with isolated short fixtures; do not automatically export a real chapter. Show the ready preview page in the external browser, give one run command and stop. No automatic commit or push.

# Current approved build: Stage 6 Audio

- Include a single seekable full-chapter audio player assembled locally from current beat clips without TTS; invalidate/rebuild it when any clip changes and retain older files.
- Build a separate generic Stage 6 Audio page using only valid imported Stage 5 narration. Use Andrew Multilingual, 1.2x speed and measured 180 ms period pauses. Generate sequentially with real word/phrase timestamps, safe stop/resume, chapter playback and individual-beat regeneration. Isolate new audio and jobs from historical media, preserve completed clips and older regenerated revisions, reject stale sources, and retain the exact script. Use the dark glass palette and shared dropdown geometry. Do not run detection, prepare panels, rewrite/import narration, generate video or automatically synthesize real chapters. Generation starts only through explicit controls. Test with isolated fixtures, show the running external-browser page, give one run command and stop. No automatic commit or push.

# Current approved build: Stage 5 Narration

- Build the separate generic Stage 5 Narration page from current Stage 4 prepared panels. Export labeled photos in plain folders of at most 20 in natural page order; never ZIP them. Prompts must be copyable in the page, not downloadable. Include the narration master, exact IDs, preceding saved cast/context, previous narration ending and a version receipt in each batch prompt. Support fresh AI chats, story beats with optional panel selection, JSON validation and targeted corrections. Save successful narration, cast and provenance-backed continuity atomically; block stale/dependent imports after upstream changes. Keep the new workflow's saved state separate from historical narration/media. Keep the Stage 4 contact sheet hidden as requested, with folder and Export & narrate actions. Use dark glass and the shared select spacing. Do not run detection, prepare chapters, call TTS, generate video or import real narration automatically. Show Stage 5 in an external browser, give one run command and stop. No automatic commit or push.

# Current approved build: Stage 4 Panel Review

- Build Stage 4 from saved Stage 3 detections only: selectable chapters, panel crops with 1% padding, unique IDs such as `p0000-1`, whole-page labeled copies, flagged-first review contact sheets, and per-page add/delete/reorder/resize editing. Save fixes independently of detection and regenerate only the saved page. Preserve manual fixes and stale edited data; never overwrite them silently. Use dark glass and shared dropdown spacing, native folder buttons and resumable background preparation. Do not run Magi, OCR, narration or later stages. Do not automatically prepare real chapters during implementation; the user starts selected chapters explicitly. Test with isolated fixtures, show the external-browser page and stop.

# Current approved build: Stage 3 Panel Detection

- Build the approved separate Stage 3 page with manga/chapter selection, saved reading direction, a sequential background Magi v3 worker, accurate progress, safe stop/resume, content/model/direction fingerprint checks, valid ordered panel JSONs, numbered previews, flagged reports and native output-folder buttons. Use only normal model detection and discard all non-panel boxes immediately. Do not run real chapter detection during implementation; the user starts selected chapters explicitly. No cropping, OCR, character association, narration or other new stages. Preserve existing inputs and older outputs. Support Mac and Windows, show the running page in an external browser, give one run command and stop.

## Chapter folder naming

- Download chapter folders use readable consistent names such as `Chapter 1`, `Chapter 2`, `Chapter 2.5`, without generated hash/ID suffixes. Keep source IDs in JSON metadata only. Reject duplicate chapter-number releases or destination conflicts instead of appending suffixes or overwriting.

# Current approved step: Stage 2 Renumbering

- Implement chapter-folder selection and explicit renumbering of remaining images from `0000` in natural filename order, preserving original extensions and never overwriting files. Keep the approved dark glass styling. Renumber immediately when the user presses the button, without a preview step. After success, provide right-aligned native Open folder buttons per chapter. Operate only on the user-selected repo-local chapters; do not delete images, run detection or change other stages. Test with isolated fixtures, show the page and stop. No automatic commit or push.

# Local storage and platform requirements

- Support macOS and Windows. Save downloaded images inside the current cloned repository under `manga/`, and download state under `data/`. Resolve paths from the repository location, never a hardcoded user/home/drive path. Keep both directories ignored by Git; do not push downloaded or generated files.

# Current approved build: Stage 1 Downloader

- Build the approved downloader for MangaPill and WeebCentral only, with chapter selection, verified image downloads, persistent safe stop/resume and accurate page progress. Use the dark glass palette. No PDF/CBZ, no automatic panel detection, no other pipeline changes. Preserve the existing library and media. Show the result and one run command, then stop.
- Gento was explicitly authorized as a read-only reference for the prior architecture study. Implement this downloader independently inside this repository; do not import or execute the reference project.

# Current direction: user-led rebuild and design standard

- All single-line dropdowns must use shared `app/static/controls.css`: a 14 px arrow inset and 44 px trailing text padding. Include this stylesheet after page styles in every new page. Do not rely on the browser’s edge-hugging native arrow or override this spacing locally. Preserve native keyboard interaction and forced-colors accessibility.

- The approved sidebar appearance is dark glass: Onyx and Graphite surfaces, Pearl Aqua accents, Snow text, minimal decoration. Do not switch it to a light/Snow surface.

- Follow the user's next steps individually. The existing app is still being rebuilt; do not initiate further workflow changes, additions, removals or a broad redesign without the user's specified step.
- Before adding or changing any UI, assess placement, consistency with existing controls, hierarchy, responsive layout and enabled/click behavior. Apply only a coherent design; do not append controls without considering their surrounding layout.
- All new or redesigned UI must use Apple-inspired glassmorphism: translucent glass surfaces, restrained blur, subtle highlights, layered depth and readable contrast. Keep effects lightweight for a weak laptop and use plain HTML/CSS/JS.
- Use the user's palette: Onyx `#131515`, Graphite `#2b2c28`, Verdigris `#339989`, Pearl Aqua `#7de2d1`, Snow `#fffafb`. Derive transparent surface treatments from these colors; do not substitute another palette.

# Previously approved stage: generic workflow and persistent story context

The user explicitly approved making this app generic for daily use across manga and chapters, with persistent story memory and fresh AI chats. This supersedes the historical manga/chapter/beat allowlists below. Preserve existing user work and media. Do not automatically process additional manga, run detection, call TTS or regenerate existing videos as part of this upgrade. Allow these actions through explicit UI controls for any registered chapter. Keep every input inside this repo, free tooling, panel-only detection, plain HTML/JS, FastAPI and JSON storage. Keep each manga/chapter's assets and tasks isolated. Save original batch/chapter memory with provenance, carry only preceding valid history, keep uncertainty explicit, bound prompt context, and invalidate dependent imports after upstream changes. Show the completed UI and one run command, then stop. No automatic commit or push.

## Historical approved stages (scope restrictions superseded above)

# Project Rules

- This project is a local web app that turns manga chapters into recap videos.
- Always open this project's web app in an external browser. Never open it in the Codex in-app browser, including for previews or UI verification.
- Build everything from scratch inside this repo. Do NOT read, open, copy or import anything from any other folder or project on this computer, including old Rune or Magi project folders.
- The plan is fixed. Work in stages. Do ONLY the stage the user gives you, then stop, show what you built, give the user one command to run it, and wait for their approval. If you think a stage should change, tell the user and wait. Never change the plan yourself.
- Use only free tools. Run on a weak laptop. Python backend (FastAPI), plain HTML/JS frontend, no heavy frameworks. Store data as JSON files on disk. The app runs at 127.0.0.1.
- Never name speakers in code. Character names come only from the AI narration the user imports.
- Do not add features the user did not ask for.
- Keep manga images, outputs, .env and virtual environments out of git.

## Stage 1 detection rules

- Magi v3 may run its normal detection, which outputs panel, text and character boxes. Save ONLY boxes labeled "panel" to page JSON. Never save or use text boxes, character boxes, OCR, speaker guesses or character associations anywhere. Discard non-panel detections immediately; never run OCR or association routines.
- Do not force panel-only decoding. Use normal model detection and retain only panel-labeled boxes.
- A page JSON is valid only when it has at least one panel, every box is inside the original page bounds, and no box covers less than 1% of the page area. Pages that fail belong in the report's flagged list, never in valid output.
- Stage 1 is approved. Processing is authorized only for all pages of Chapter 1 and Chapter 12 of the registered manga. Save a red-box preview numbered in reading order for every page. Do not process any other chapter. Keep the Mac awake while processing and show progress in Library. Report pages, valid pages, flagged pages, total panels, average seconds per page and total time for each chapter, then stop.
- Processing may be enabled only for the two explicitly approved chapters, Chapter 1 and Chapter 12. All other chapters remain disabled. Approval of these runs does not authorize additional chapters.

## Stage 2 scope

- Stage 2 uses only saved panel JSON for Chapter 1 and Chapter 12 of the registered manga. Do not run detection or process another chapter.
- Save panel crops with IDs such as p003-1, in reading order, with 1% of each panel's width/height as padding on each side, clamped to page edges. Save a whole-page copy labeled with large panel IDs.
- Zero-panel pages use the whole page as one panel and remain flagged. Flag boxes covering at least 90% of a page with other boxes, actual intersections whose width is over 5% of page width AND whose height is over 5% of page height, widths/heights under 10% of page dimensions, areas under 3%, and spreads (width greater than height).
- Provide chapter review contact sheets, flagged pages first with reasons. Support reorder, resize, delete and draw; save fixes separately from Stage 1 and regenerate only that page's crops and labeled copy. Print chapter page/panel totals and flagged reasons, then stop.

## Stage 3 scope

- Revised Stage 3 is authorized for Chapter 1 only. Use existing labeled pages and panel IDs; do not process another chapter or rerun detection/cropping.
- Export at most 20 labeled pages per ZIP. Each batch prompt includes the updated narration master, an exact valid-ID manifest, imported cast, and only preceding saved story context plus the previous narration ending.
- Use story beats containing one or more selected panel IDs. Unused panels are allowed. Reject unknown/repeated IDs and reading-order violations within/across beats. Keep the narration budget at 40 words per selected panel per beat. Legacy panel-by-panel replies must not be silently converted.
- Carry imported continuity summary and open_threads forward. Import earlier batches before generating subsequent prompts; changed earlier narration invalidates dependent batch context. Do not add intros, batch/chapter conclusions or outros. Only an explicit final-recap choice permits a supported closing.
- Ground narration in supplied pages. Never invent IDs, events, dialogue, thoughts, motives, relationships or names. Mark uncertainty or omit unsupported claims. Code validates structure/IDs; a human still reviews factual accuracy and natural flow.
- Failed drafts do not change saved narration, continuity or cast. Provide exact-ID/beat errors and copyable targeted corrections; preserve unaffected beats. Successful imports commit beats, cast and continuity together. Test the new format on Batch 1, show the updated UI, then stop.

## Stage 4 scope

- Stage 4 is authorized for Chapter 1 only, using its imported story beats. No new chapters, detection, or cropping.
- The user explicitly approved Edge TTS after being told it sends narration to Microsoft's online service and requires internet. Keep the app and generated MP3s local; no paid API.
- Provide audio playback and individual-beat regeneration. Generate sequentially, preserve completed clips on failure, and invalidate audio when its narration changes. Then show the UI and stop.

## Stage 5 scope

- Stage 5 is authorized for Chapter 1 only, using existing selected panel crops and approved narration audio. Do not add chapters or rerun detection, cropping or TTS.
- Start with equal panel timing per beat; provide an audiovisual preview, adjustable switch times saved as JSON, and MP4 export with simple cuts and unstretched images.
- Invalidate exports after timing or source changes. Keep rendering lightweight and local. Show the completed UI and video, give one run command, then stop.

## Sentence/phrase alignment revision

- Approved for Chapter 1 only, keeping the exact imported script, Andrew Multilingual voice, and short-pause settings.
- Map exact unique phrases to existing selected panels in reading order; capture real Edge TTS word timestamps and adjust them for removed silence.
- First test only beat 6 (truck, driver, rescue), with an isolated audio/video trial. Keep the existing chapter narration, audio manifest, numeric timing and full MP4 unchanged.
- Do not regenerate the whole chapter until the user reviews and approves the single-beat result. Do not process new chapters or rerun detection/cropping. Show the trial and stop.

### Additional alignment previews

- The user requested multiple examples. Additional isolated previews are approved only for Chapter 1 beats 10, 19 and 25, alongside the existing beat 6 trial.
- Show the four examples together for review. Keep the exact script, Andrew voice, short pauses, existing chapter audio and MP4 unchanged. Full-chapter regeneration remains unapproved.

### Approved full Chapter 1 alignment

- The user approved applying phrase alignment to all 30 Chapter 1 beats and regenerating its MP4. This supersedes the earlier sample-only restriction.
- Preserve the exact script, selected panels and their order, Andrew voice and short-pause profile. Reuse current timestamped samples; synthesize remaining beats sequentially. Keep completed samples for safe resumption and retain the previous video until the replacement passes validation. No other chapters, detection or cropping.

### Approved Chapter 1 speech pacing revision

- The user requested 1.2× or 1.3× narration and a slight pause after periods. Use 1.2× Andrew speech with measured 180 ms period pauses for Chapter 1 only, preserving the script and phrase maps.
- Re-capture actual word timestamps at the chosen rate and account for all silence removed or inserted. Rebuild synchronized audio and MP4; preserve the prior video until the replacement is validated. Individual beat regeneration must retain the saved pacing settings. No other chapters or detection/cropping.

### Approved Chapter 1 background revision

- Replace black video borders with a full-frame, blurred and dimmed copy of the currently displayed panel. Keep the center panel sharp and unstretched, with a soft shadow and subtle edge for separation.
- Use the same composition for the live preview, existing beat previews and Chapter 1 MP4. Preserve the script, audio files, 1.2× Andrew rate, period pauses and panel switch times. Do not run TTS, detection or cropping, or process other chapters.

### Approved Chapter 1 motion revision

- Add subtle slow zoom-in, zoom-out, slide-from-left and slide-from-right camera movement to Chapter 1, varying across panels. Retain the blurred current-panel background and foreground depth.
- Keep the entire sharp panel visible. Limit motion on short holds. Clock live-preview movement to narration, including pause and seek; render the same motion at the saved cuts without cumulative frame rounding drift.
- Preserve all narration, audio, 1.2× Andrew rate, period pauses, panel IDs/order and saved switch times. Update Chapter 1 MP4 and existing beat previews using local rendering only. No TTS, detection, cropping or new chapters.

### Chapter 1 motion jitter fix

- Replace pixel-rounded moving crops with fractional-pixel affine sampling. Verify frame-to-frame movement, rebuild Chapter 1 and existing previews, and preserve all audio, narration and cut times. No other chapters.

### Chapter 1 livelier motion revision

- The user approved slightly more noticeable zooms and slides, without excessive effects. Increase movement moderately while retaining fractional-pixel smoothness, the full sharp panel, existing background depth, exact audio and panel timing. Rebuild Chapter 1 and its beat previews only.

### Chapter 1 subtle panel transitions

- Add brief crossfades starting at the existing panel switch frames, including between beats. Cap them at four frames (about 0.17 seconds at 24 fps), shorten for very brief holds, and keep the outgoing panel frozen at its final camera position. Preserve exact audio, saved timings and total duration. Update the live preview, Chapter 1 MP4 and existing beat previews only. No new chapters, TTS, detection or cropping.

# Current approved audio revision: grouped Gemini narration

- Gemini Charon is the main option for new long-form audio selections. Combine selected chapters in natural order into balanced roughly 7–8 minute sections across chapter boundaries, keeping scripts unchanged. Save each returned WAV before free local English forced timing, publish compatible chapter/beat copies for Video, and provide one full selection track. Preserve all existing chapter audio, video, narration and prior revisions. Never automatically generate real speech during implementation or startup.
- Use the existing ignored `.env` key and repo-local model cache. Enforce a conservative local request budget, distinguish daily quota, temporary rate limits, key/access, network and local timing failures, display reset time when known, and require explicit resume. Timing retries must reuse saved recordings. Brian at 1.0× with 180 ms period pauses is an explicit backup only; never silently switch voices. Keep sequential heavy-task exclusion, persisted recovery, dark glass controls and external-browser verification. Do not commit/push automatically.

# Approved Gemini key rotation

- Support the original and numbered GEMINI_API_KEY entries in the ignored .env, deduplicate secrets and keep them out of responses/jobs/logs. Switch to another available key only after confirmed quota/access rejection or a reached local daily budget. Persist per-key cooldowns, daily counters and rejected-access state; show available counts and explicit exhaustion/reset errors. Never rotate/resend after ambiguous timeouts, malformed replies or server failures. Keep Charon/script/checkpoints unchanged, preserve saved recordings and require explicit Brian fallback. Keys on one Google project share quota. Test with isolated mocked responses; no automatic real speech, browser interaction, commit or push.
