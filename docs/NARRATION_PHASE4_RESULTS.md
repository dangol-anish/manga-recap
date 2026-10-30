# Narration Phase 4: controls and progress

Scope: Stage 5 controls, visible feedback and progress. Synthetic fixtures only. No real chapters, Gemini requests, personal browser windows, app-server restart or extension installation/reload.

## Changes

- Narration actions give immediate dismissible toast feedback, then success or failure. This includes pairing, connection checks, folder opening, catalog/status refresh, queue Start/Resume/Retry/Stop, desktop-alert permission and sound activation. Existing manual narration controls receive click/request feedback and forward section messages into the toast region.
- Queue controls disable conflicting actions and preserve available selections when refreshing chapters. Stop remains independent of an in-progress Start/Resume request and does not require a browser connection. Failed requests release their controls. Older status snapshots cannot replace a newer queue checkpoint.
- Requests initiated through the queue controls, and manual actions covered by feedback, have a 20-second response timeout. Timeout messages explain that the action may already have been accepted, direct users to read saved status, and never automatically resend it.
- Each queue shows current chapter/batch, actual activity, saved-batch progress, recorded active time and current-step time. Extension activity distinguishes opening a chat, configuring settings, checking schema persistence, inserting a prompt, attaching/uploading photos, submitting and reading a reply. Validation/correction/saving remain separate activities.
- Active time is checkpointed and excludes paused time. Recovery counts only recorded time through the last checkpoint. Older queues explicitly show unavailable timing rather than invented historical elapsed time.
- Persistent warnings flag missing app updates after 12 seconds, a missing paired-browser heartbeat, slow navigation/submission after 30 seconds, uploads after 120 seconds, generation after 180 seconds and other steps after 60 seconds. These are attention warnings, not proof of a stall; existing hard timeouts remain separate. Warnings neither resubmit nor start repairs.
- An independent authenticated heartbeat runs during long extension commands so uploads/settings work do not falsely look disconnected. Progress accepts only known activities for the currently claimed command and is recorded only by the queue worker.
- Notification and sound failures cannot interrupt queue rendering. Toasts use the existing palette, accessible status/alert semantics, dismiss controls and a three-card limit, with narrow-screen verification.

## Evidence

- 87 focused Python tests passed: progress timing, narration queues, extension transport, response handling, validation, errors, shared controls and documentation.
- Actual unpacked-extension browser fixture passed (58 seconds). Its AI Studio responses are local synthetic fixtures; no Google requests or existing browser windows.
- Rendered Stage 5 feedback browser fixture passed after final edits (1.7 seconds). All requests are intercepted, external requests blocked. It verifies one explicit Start, a usable Stop, no automatic resubmission, current activity, elapsed time, long-generation warning, paused-warning cleanup, API-failure feedback, retained selection and desktop/390-pixel toast layout. Only explicit Start and Stop issue writes.
- Four JavaScript checks passed: browser queue UI, manual narration UI, story/panel preview and story memory. JavaScript syntax and git diff whitespace checks passed.

Logs: `/tmp/phase4-final-python.log`, `/tmp/phase4-browser.log` (extension fixture passed; initial feedback-test fixture decoding error was corrected), `/tmp/phase4-feedback.log` (final feedback fixture passed).

This is source and synthetic-test evidence, not live AI Studio validation. Changes are not deployed to the already-running app or installed extension. No commits or pushes. Queue history/clear controls are outside this phase.
