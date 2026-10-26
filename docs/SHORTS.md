# Shorts

Shorts is included on `main` at `/shorts`. It never reads chapter MP4s, rewrites chapter narration or changes chapter audio/timing. Inputs are current imported narration and prepared panels. No Gemini API/key is needed.

1. Choose one manga and any ready chapters, then **Prepare Gemini package**.
2. **Save source file**, **Copy Gemini prompt**, and attach/paste them in a fresh Gemini chat. In AI Studio, optionally use **Copy output schema** with Structured outputs. No images are required for the first analysis.
3. Paste Gemini's complete JSON into the app and **Check & save suggestions**. Zero suggestions is a valid answer. Invalid replies leave saved work untouched.
4. Review the scripts and panel sequences. For an optional factual/visual check, use **Check these panels with Gemini**. It opens only the relevant labeled pages and copies a matching prompt. Attach those images, then import its JSON using the corresponding **Visual-check correction** reply type. This replaces only that proposal.
5. Select ideas and **Create selected shorts**. New projects use Brian speech at 1.2× and 180 ms period pauses through the online Edge service; existing projects retain their saved settings. Voice clips are saved individually and reused on retry. The app creates fresh 720 × 1280, 30 fps MP4s with fractional motion, hard cuts at segment boundaries and timed phrase captions.
6. Play the actual MP4 in the native player; **Save video to computer** or **Open Shorts folder**. To adjust the narration, edit segments and **Save script**, then create again. Old media remains on disk.

## Data and safety

- Local project snapshots/history/jobs: ignored `data/shorts/<project-id>/`.
- Clips, optional visual-check pages and MP4 revisions: ignored `outputs/Shorts/<project-id>/<short-id>/`.
- Each spoken segment contains a chapter, source beat, panel ID and 1–40 words. Short scripts contain 60–280 words. No fragile phrase-anchor strings are requested; speech is captured for each segment, so its panel change follows actual audio duration.
- Actual final duration must be 30–120 seconds. Estimates are approximate. If speech falls outside that range, edit the script; saved clips are retained rather than silently truncating or stretching it.
- Receipt/source hashes bind the response to its package. Chapter, beat and panel membership and chronological order are checked. Selected source changes mark the project stale and block new creation/download; prior files remain intact in its folder. Start a new package after source changes.
- Selection runs sequentially and shares the heavy-task exclusion with other stages. Stop waits for an in-flight speech call (bounded by the existing service timeout); FFmpeg can be terminated. Restart never automatically resumes generation. Choose the unfinished shorts and create again.
- Text-only analysis can identify a narrative moment, but cannot certify unseen visuals or correct errors already present in imported narration. Structural validation is not a factual AI verifier. The optional labeled-page check and the visible panel/script preview address that limit.

Suggestions are not a guarantee of popularity or factual accuracy. Existing chapter work stays separate; no media migration is required. See [Shorts review](SHORTS_REVIEW.md) for source comparison, captions, timing estimates and safe draft discard.
