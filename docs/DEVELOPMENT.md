# Development and testing

The app uses Python 3.11, FastAPI, plain browser JavaScript and JSON storage. Use the setup guide for [Mac](MAC_SETUP.md) or [Windows](WINDOWS_SETUP.md) first. [Architecture map](ARCHITECTURE.md).

## One regression command

From this clone on macOS:

```sh
.venv/bin/python -m scripts.run_tests
```

From PowerShell on Windows:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.run_tests
```

Install Node.js (the CI uses Node 22), FFmpeg and ffprobe on PATH, in addition to the Python environment. Node is for the control tests, not for running the app. The runner checks these prerequisites before starting, runs Python's unittest suite and every `tests/test_*.cjs` script, and returns a failing exit code if any check fails. It uses the current Python interpreter, resolves paths from the repository, and does not rely on shell-specific loops. CI uses the same command.

Tests use isolated synthetic chapter fixtures and mocked model/provider calls. Some tests create small real media with local FFmpeg. They do not perform real manga detection, call live TTS or open your browser. Windows may skip symlink fixtures without symlink privileges; read skips as limitations, not proof of those paths. Error tests may append redacted diagnostic entries; user manga, narration, project JSON and media must remain unchanged.

For an affected area while editing, run its tests directly, for example:

```sh
.venv/bin/python -m unittest tests.test_shorts tests.test_shorts_review -q
node tests/test_shorts_ui.cjs
node tests/test_shorts_review_ui.cjs
```

Run the shared complete command before delivering the change. `git diff --check` checks accidental whitespace. Syntax checks: `python -m compileall -q app scripts` and `node --check <file.js>`.

## Installation checks are separate

`python -m scripts.doctor --media` verifies the installed environment and creates a one-second synthetic MP4. `python -m scripts.validate_model` explicitly loads Magi on a synthetic image and saves its validation record. Model installation may download large files. None of these commands is an app-startup action, and the regression runner does not download models or make live service calls.

## Safe changes

- Never use a real manga or saved project as a disposable fixture. Do not synthesize speech, create videos, restore, clean up or migrate user files merely to test a feature.
- Preserve route contracts, JSON schemas, receipts, source hashes, saved voice settings, job snapshots and output names unless the user approved a migration.
- Preserve the historical routes/modules listed in the architecture guide. A legacy filename alone does not make code unused.
- Shared dropdown geometry belongs in `app/static/controls.css`, loaded after page styles. Use the existing dark palette and native media playback.
- Test HTTP errors with isolated state. Shared `app.errors.request_error` preserves current workflow conflict/input status codes; historical Library mappings intentionally differ.
- Keep `.env`, manga, narration, jobs, recordings, videos, model files, backups and outputs ignored. Never put real keys into examples or test logs. Commit/push only when explicitly requested.

## Documentation map

The README is the short starting point; `WORKFLOW.md` owns detailed stage behavior. Focused guides own memory, audio, video review, Shorts and backup/recovery. Keep labels/defaults consistent with the actual page and frozen-job semantics; new defaults must not be described as changes to existing recordings. The Windows audit is historical verification context, not a substitute for current CI or local platform testing.

## Browser layout regression checks

See [Shared UI standards](UI_STANDARDS.md) for development-only browser setup, isolated 17-page layout checks and CI screenshots. The app's runtime/install requirements are unchanged. These browser checks complement the regular Python and JavaScript control tests; they use no saved manga, recordings, videos or Shorts.
