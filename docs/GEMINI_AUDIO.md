# Continuous audio with Gemini

1. Finish and import current Narration batches for the chapters you want.
2. Open **Stage 6: Audio**, choose a manga, and tick those chapters.
3. Press **Create selected audio** once.
4. Listen to each returned section, or the **Full selected audio** when finished.
5. Continue to Video; the chapters have compatible clips and phrase timings automatically.

Repeating the same completed Gemini selection reuses its saved audio instead of spending another request. The script is unchanged. Whole story beats are grouped across chapter boundaries, usually around 1,250 words per request (roughly 7–8 minutes, depending on delivery). A single short chapter does not consume a separate request when it fits beside others. The last section is balanced with the others. Voice: Gemini Charon, matching the approved samples. Google receives narration only, not manga images. Internet is required. Gemini delivery and pauses are natural; unlike the Brian backup, pauses are not artificially fixed to 180 ms.

## Setup

Save `GEMINI_API_KEY=your_key` in `.env` at the cloned repository root. Add extra keys as `GEMINI_API_KEY_2=...`, `GEMINI_API_KEY_3=...`, and so on. Never commit this file. Keys are read when used, so newly added keys are available on resume without restarting. Duplicate key values count only once. Use your free-tier Google project with billing disabled; the app neither enables billing nor switches to a paid service. Google owns billing/quota settings; an API key does not let this app verify them. The tested project's quota was 3 requests/minute, 10,000 input tokens/minute and 10 requests/day. Limits can change or differ by project, and other keys on that project share them. The app has an additional conservative local budget of 10 attempted requests per key per Pacific day, including uncertain failed attempts. It waits between sections. Confirmed quota/access rejections pause that key and try another available key once for the section, keeping the same model, voice and frozen script. Daily pauses last until the Pacific reset; temporary limits respect Google’s retry delay (at least one minute). Invalid keys/model access remain disabled until the key is replaced. Cooldowns and attempted-request counts survive restart. Keys on the same Google project still share its quota; rotation does not create quota.

Existing Python dependencies and FFmpeg are sufficient on macOS and Windows. On first use the free English speech timing model downloads about 360 MB into ignored `vendor/speech-alignment/` inside the repository. It runs on CPU with two threads. No paid transcription service is used. Model: `facebook/wav2vec2-base-960h`, pinned revision `22aad52d435eb6dbaf354bdad9b0da84ce7d6156` (Apache 2.0). Local forced alignment estimates actual word boundaries against the frozen script; it is not a guarantee that synthesized speech or visual storytelling is perfect. Listen to the result before publishing.

## Stops and errors

**Stop safely** waits for an already-submitted Google request to return and saves its recording before stopping. An HTTP request in progress can take up to 15 minutes to time out; closing the terminal forcibly can lose an in-flight response. Restart leaves unfinished work paused and never starts generation automatically.

Errors identify whether the problem is a daily quota (with a local reset time), a temporary request/token/capacity limit, rejected key/access, connection/timeout, incomplete recording, changed narration, or local timing. Google may count failed/timed-out requests. Completed recordings stay saved. **Resume Gemini** uses an available key for unfinished recordings; it does not resend recordings already saved. A timing/model-download failure retries locally from the WAV.

**Use Brian for remaining sections** is a deliberate backup. It keeps already-returned Gemini recordings, so a mixed selection can contain both voices. Brian uses 1.0× speed and measured 180 ms period pauses. The app never changes providers silently.

## Storage and compatibility

Jobs and checkpoints: ignored `data/grouped-audio/`; compatible current-chapter pointers: `data/gemini-chapters.json`. Returned WAVs, frozen scripts, new beat clips and full MP3s: ignored `outputs/grouped-audio/`. Readable copies appear in the same flat `outputs/Audio/<manga name>/` folders as existing chapter audio. Previous files are retained, including user-edited copies.

Only completed chapters become available to Video. New audio invalidates previous video/timing status, while all old MP4 files remain intact. Changed upstream narration blocks resumption and serving stale selections; create a new selection for the new script. Individual Edge regeneration is disabled for a chapter using a grouped recording, preventing accidental voice changes. The expandable chapter section still plays old clips and provides the legacy Brian workflow for other chapters. Shorts retains its existing Brian generation.

Official references: [Gemini speech generation](https://ai.google.dev/gemini-api/docs/speech-generation), [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), [model license and documentation](https://huggingface.co/facebook/wav2vec2-base-960h).

The Audio page shows configured/available key counts, paused-key reasons and reset times. If every key is unavailable, recording pauses with `all_keys_unavailable`; add/replace a key, wait for reset, or explicitly choose Brian. Timeouts, unreadable responses and server errors do not rotate or resend automatically because their completion may be ambiguous. Only key variable labels and hashes go into job/quota metadata, never credential values. Rotating keys does not change billing settings or guarantee a fixed combined quota.
