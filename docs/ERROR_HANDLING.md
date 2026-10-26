# Understanding an error

Errors now identify the stage and action, the affected page/chapter/beat/section or file when known, the actual cause, recovery steps and what previously saved work remains available. Each report includes a diagnostic reference. No error handler resets your library, deletes manga, switches voices or automatically repeats an import/speech request.

Examples:

- **Disk full:** free space on the repository's drive, then resume unfinished work. A failed HTTP write returns 507 instead of an unexplained 400.
- **File in use on Windows:** close the specific file in players/editors/Explorer preview and retry. Permission errors separately explain folder access.
- **Broken saved JSON:** the message names the file and line/column (or invalid UTF-8 encoding). The app keeps the damaged file intact; it does not silently replace it with empty state.
- **Downloader errors:** website HTTP status, connection failures, blocked access and affected page are reported. Permanent filesystem/access failures are not repeatedly retried as if they were transient network failures.
- **Detection:** model failures and individual-page failures identify the page. A worker terminated outside Python reports its exit code and available worker-log output, including a memory-pressure hint for a forced termination.
- **Panel preparation/renumbering:** failures retain page/transaction context and explain how to resume without manually deleting recovery files or overwriting images.
- **Narration:** field validation identifies the bad field; existing exact panel-ID, receipt, context and correction errors remain intact. Unexpected import/save errors include a diagnostic reference.
- **Audio:** missing/stale narration is shown beside unavailable chapters. Gemini provider/quota/key/free-tier errors retain their specific explanations; local timing and media conversion errors retain their cause. Failed progress checkpoints show a visible warning, even if the disk cannot save a log.
- **Video/Stitch/Shorts:** unavailable inputs explain the blocker, and failed external tools include the actual exit status and stderr when available. Earlier completed media is retained.
- **Browser connection/player errors:** the interface explains whether the app cannot be reached, an HTTP response was rejected, a response was malformed/incomplete, or a media element could not load/decode its file. It tells you to check task progress before repeating an action that may already have reached the server.

## Diagnostics and recovery

Technical details are saved only inside ignored `data/errors/YYYY-MM-DD.jsonl`. Search for the reference shown in the interface. The current app process also retains the most recent 100 reports in memory, readable locally at `/api/errors/<reference>`. Disk/permission errors that prevent diagnostic logging still produce an in-memory report. Persisted daily log growth is capped at about 10 MB; further reports remain available in memory.

API keys and credential-like strings are redacted, and URL queries are removed from diagnostic text. Error logging does not send data to a third-party service. Browser errors are displayed locally; they are not uploaded automatically.

A damaged saved task is kept in place and surfaced as a warning. Other readable task files can still be listed/recovered. Progress-save failures release the worker's busy state rather than leaving it stuck forever. Startup recovery and shutdown errors identify the affected phase instead of hiding the original failure behind a secondary cleanup exception. After fixing a startup recovery error, restart the app to recheck saved tasks.

For renumbering, an interrupted transaction can legitimately have images in its temporary folder. Keep that folder together with the chapter and use normal recovery; do not delete it. For online TTS timeouts, Google/Microsoft may have processed the request even if no response was returned. The app never automatically repeats an ambiguous request or silently chooses Brian.

## Verification limits

Tests simulate failures with isolated files and mocked services; they do not damage real manga or exhaust real quotas. The shared error layer provides context and a diagnostic reference for unexpected failures, but it cannot infer an unknown provider outage or promise that every possible operating-system failure is automatically repairable. Its job is to explain the observed failure and preserve a safe recovery path.
