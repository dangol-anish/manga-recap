# Narration Phase 5: safe queue clearing

Stage 5 only. Synthetic tests and temporary fixture data only. No Gemini requests, real chapters, existing browser windows, app-server restart, installed-extension changes, commits or pushes.

## Behavior

- Clear queue appears beside the existing queue actions for paused, failed and completed queues. Its nearby explanation states exactly what remains saved.
- Clearing resets the visible queue and errors, retaining the selected chapters so a new selection can be started explicitly. It does not start any work, disconnect the extension or navigate a browser tab.
- Saved narration, cast/story memory, accepted imports, replies and queue files remain on disk. Only the current-queue pointer is cleared atomically. Reload/startup therefore does not resurrect the dismissed queue.
- Start continues to reuse current accepted batches. Clearing does not reset their imported status or create a request to regenerate them.
- Running/stopping queues cannot be cleared: Stop first, then wait for paused status. A paused worker is signalled to exit and joined outside the queue lock before the pointer is cleared. A worker that has not exited leaves the queue intact with an actionable error.
- The current queue identity/status is checked again after the worker wait. A changed queue cannot be accidentally cleared by an old button request.
- Disk/checkpoint failure retains the visible current queue and produces error feedback. No automatic write retry occurs. Older in-flight status reads are ignored after a successful clear so they cannot restore old progress on screen.
- Existing dark-glass controls, toast feedback and single-action busy protection are reused. No destructive delete/reset of narration is offered.

## Evidence

- 92 focused Python tests passed, including five new clear-control tests: failed/completed/paused persistence; running/stopping/wrong-identity rejection; pointer write failure; paused-worker shutdown ordering; busy-worker rejection.
- The existing synthetic accepted-import/reuse test was strengthened to clear a completed queue, compare saved narration/context unchanged, retain reply files, start a new queue and prove the fake provider send count does not increase. This and all five clear tests passed again after the final test change.
- The isolated rendered browser fixture passed: Clear is hidden while running; failed clearing leaves progress and reenables controls; completed clearing hides progress and shows success; selected chapters remain; Start becomes available; reload stays cleared. Exactly four explicit writes occur (Start, Stop, failed Clear, successful Clear), with no automatic submissions. Network routes are intercepted and external requests blocked.
- Four JavaScript checks passed: queue controls (including failed queue clearing), manual narration, panel preview and story memory. Python compilation, JavaScript syntax and git diff whitespace checks passed.

Logs: `/tmp/phase5-python.log`, `/tmp/phase5-reuse.log`, `/tmp/phase5-browser.log`.

Changes are implemented in source, not deployed to the already-running app. These results establish synthetic behavior, not live AI Studio reliability. No further phase was started.
