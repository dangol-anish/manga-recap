# Narration through your AI Studio tab

Stage 5 has an optional **Create narration in AI Studio automatically** queue. It uses a small local Chrome/Brave extension in your ordinary browser, with no Gemini API or API key. The manual photo/prompt/import workflow remains available.

## One-time extension setup

1. Run the app normally and open Stage 5 Narration. Expand **Create narration in AI Studio automatically**.
2. Press **Open extension folder**. In Chrome open `chrome://extensions`; in Brave open `brave://extensions`. Turn on **Developer mode**, press **Load unpacked**, and select the repository's `browser-extension` folder. On another computer, use that clone's folder; no hardcoded home path is used.
3. Open [AI Studio](https://aistudio.google.com/prompts/new_chat) in your usual browser and sign in yourself. The extension does not operate on Google sign-in pages. If AI Studio was already open before installing the extension, reload that tab once.
4. In the app press **Get pairing code**, then **Copy pairing code**. Open the Manga Recap Narration extension from the browser's extensions menu while on your AI Studio tab. Paste the code and press **Connect this AI Studio tab**.
5. Return to Stage 5 and press **Check connection**. It should say connected. Pairing codes expire after five minutes and work once. Pair again after restarting the local app. Use **Disconnect extension** while the queue is stopped to revoke access.

No Playwright package or downloaded automation browser is needed for this workflow. The earlier dedicated Playwright browser encountered Google's sign-in rejection and is no longer the queue's connection. It remains only as a legacy adapter and synthetic development test. Do not change browser identity flags, bypass security checks, or copy cookies from personal profiles.

## Start a selection

Choose a manga and one prepared chapter for the first supervised test, then press **Start narration queue**. This explicitly authorizes sending that selection's current prompts and labeled photos to Google AI Studio. Current valid imports are reused. Nothing starts automatically when opening the app or restarting it.

The queue opens a fresh chat for each batch (at most 20 photos), checks Gemini 3.8 Flash and Medium thinking, disables unrelated tools, and enables Structured outputs on every fresh chat. It supplies the explicit narration-field schema from `browser-extension/narration-schema.json`, supporting full batches and numbered partial corrections. The live editor can retain its example schema even after a programmatic fill, so wrapper handling remains necessary; direct-schema persistence is not yet established. Empty schemas can restore AI Studio's example `response` string wrapper, so they are not used. A completed older reply inside that exact wrapper can be unwrapped without changing its narration or bypassing any import checks. Completion does not require the empty prompt's Run button to be enabled. A missing model/control pauses for manual attention instead of silently changing models. Accepted narration, cast and continuity are saved together before the next prompt is built with the updated preceding story context. No audio or video generation starts.

Keep the local app running, the paired browser and AI Studio tab open, and the laptop plugged in with the lid open. The worker prevents idle system sleep during processing. The tab can stay behind other tabs, although browser background throttling or discarded tabs can delay progress. Use a separate tab for other work; do not type into the queue's selected tab while it is processing. Keep Narration open for optional desktop alerts and sound. Desktop alert permission is requested only through **Enable desktop alerts**.

## Pauses, stop and recovery

- Quota, access checks, sign-in, changed controls and persistent JSON errors pause with the affected chapter/batch. Complete human checks yourself and press **Resume queue** only when ready. There is no account rotation or automatic quota retry.
- **Stop queue** retains saved work and leaves your personal browser/tab open. An already submitted request may still finish remotely. Stop does not close other tabs or guarantee cancellation of a request already accepted by Google.
- Each browser action is delivered once. A missing acknowledgement never automatically resubmits it. Late/expired sends are rejected. A browser crash or ambiguous send requires inspecting the last request, then reopening its saved conversation or explicitly checking **submit this batch again** if necessary. An explicit retry may create a duplicate remote reply; accepted narration is not duplicated.
- Complete raw replies are saved atomically before validation/import. Existing receipt, source freshness, ID, chronology, phrase-anchor, word-budget and story-memory checks still apply. Rejected drafts never replace accepted narration. At most two targeted corrections are requested in the same chat. Persistent validation failures require the existing manual correction controls.
- Source/prompt/memory changes require a new selection; completed imports are retained. App restart marks unfinished work paused and sends nothing. If an import committed before its queue checkpoint, start a new selection to reuse it.
- The queue shares heavy-task exclusion with other stages and releases it while paused. Resume checks sources and busy state again.
- If the extension disconnects, open its popup, check its message and local app availability, then check connection in Stage 5. Pair again after an app restart. If you close the paired tab, open a new AI Studio tab and pair that tab using a new code after stopping the old queue.
- **Couldn't sign you in / This browser or app may not be secure:** sign in through your ordinary supported browser yourself. The extension has no permission on `accounts.google.com` and does not automate authentication. A genuine Google access restriction still requires resolution with Google; this extension cannot guarantee access.

## Scope and local files

Extension permissions cover only `https://aistudio.google.com/*`, `http://127.0.0.1:8000/*`, extension storage and a connection alarm. It operates only the explicitly paired tab. It does not read passwords, cookies, personal browser profiles or private Google endpoints. The local transport requires an extension-bound random session token; cross-site web requests cannot obtain pairing codes. Pairing tokens stay in extension storage and app memory, never jobs, prompts or diagnostics. Server restart revokes them. Pairing does not authorize generation by itself; explicit Start/Resume is still required.

Queues and raw replies remain under ignored `data/browser-narration/`; accepted narration remains under `data/manga/<id>/story-stage5.json`; labeled exports remain under ignored outputs. Older browser profiles and historical jobs are retained. The extension sends only the current exported JPEG batch, bounded to 32 MB total per action. Larger batches pause for the manual workflow. Do not include your browser profile or extension storage when sharing the project.

Tests use isolated chapters, mocked services, a synthetic AI Studio page and an actual unpacked extension in a temporary browser profile. They verify pairing, one-delivery commands, settings, upload and stop protections without real Google requests. The user-started Chapter 1 pilot completed two 20-photo batches on 2026-10-30 after fixing completed-response recognition and the exact `response` string envelope. Batch 1 was recovered from its saved conversation without resubmission; Batch 2 generated and imported automatically without a correction. AI Studio displayed 2m4.9s for Batch 2. Both retained its example response wrapper, which was unwrapped before the unchanged import checks. This establishes one successful live chapter, not reliability across every account, model or future interface. Review **Check story & panels** before audio; JSON validity alone cannot establish factual accuracy. No daily chapter count or uninterrupted operation is promised.

## Waiting for AI Studio

The extension allows up to 10 minutes total for preparing controls, schema and
photo uploads, and a separate 10 minutes for a generated reply. A slow control
can use the remaining preparation window instead of failing after 12 seconds.
Provider errors and uncertain reply states still pause safely. Stop remains
available; a timeout never automatically repeats a submission or changes saved
narration. After updating, restart the app, reload the extension on your browser's
extensions page, refresh its AI Studio tab, then inspect the tab before Resume.
