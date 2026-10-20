# Local storage and platform requirements

- Support macOS and Windows. Save downloaded images inside the current cloned repository under `manga/`, and download state under `data/`. Resolve paths from the repository location, never a hardcoded user/home/drive path. Keep both directories ignored by Git; do not push downloaded or generated files.

# Current approved build: Stage 1 Downloader

- Build the approved downloader for MangaPill and WeebCentral only, with chapter selection, verified image downloads, persistent safe stop/resume and accurate page progress. Use the dark glass palette. No PDF/CBZ, no automatic panel detection, no other pipeline changes. Preserve the existing library and media. Show the result and one run command, then stop.
- Gento was explicitly authorized as a read-only reference for the prior architecture study. Implement this downloader independently inside this repository; do not import or execute the reference project.

# Current direction: user-led rebuild and design standard

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
