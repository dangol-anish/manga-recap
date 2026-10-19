# Project Rules

- This project is a local web app that turns manga chapters into recap videos.
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
