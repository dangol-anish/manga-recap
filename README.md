# Manga Recap

A local app for turning manga chapter images into narrated recap videos and vertical Shorts. Run it from your clone in an **external browser**; Codex is not needed for daily use. Built with FastAPI, plain HTML/JavaScript and JSON files, for macOS and Windows.

## Install and run

- **Windows:** follow the [Windows setup guide](docs/WINDOWS_SETUP.md), then run `run.bat` (PowerShell: `.\run.bat`).
- **macOS:** follow the [Mac setup guide](docs/MAC_SETUP.md), then run `./run.sh`.

Open [the local app](http://127.0.0.1:8000/). Keep the terminal running while jobs work. Opening a stage does not start processing; after a restart, resume unfinished work explicitly.

## Daily workflow

| Step | What you do |
| --- | --- |
| 1. Downloader | Download selected chapters from MangaPill or WeebCentral. |
| 2. Renumbering | Delete unwanted images yourself, then renumber selected folders from `0000`. |
| 3. Panel Detection | Select chapters across manga, set reading directions and start detection. |
| 4. Panel Review | Prepare crops and labeled pages, then continue to Narration. |
| 5. Narration | Send up to 20 labeled photos and a copied prompt to an external AI; import its JSON reply. |
| 6. Audio | Select narrated chapters; Gemini creates balanced sections across chapters and a full track. |
| 7. Video | Create the MP4, watch it, adjust panel timing if needed, then save a copy. |
| 8. Stitch | Combine compatible finished chapter videos from one manga. |

Use **Chapter progress** below a stage heading to see what is ready, missing or outdated. One heavy task runs at a time. Completed work is reused where its sources still match.

**Shorts** is separate: package selected chapters' saved narration for Gemini chat, import proposals, review them and explicitly create selected vertical videos. It does not reuse long-form MP4s. [Shorts guide](docs/SHORTS.md).

See the [detailed workflow guide](docs/WORKFLOW.md) for stage behavior, stop/resume, source freshness and storage.

## Review before creating more media

- **Narration:** check the saved story beside its panels; [pin facts, verify claims and retain unresolved threads](docs/STORY_MEMORY.md).
- **Audio:** add pronunciation hints for new Gemini selections, inspect saved recordings and listen across section joins. [Audio guide](docs/GEMINI_AUDIO.md).
- **Video:** jump to panels, watch switches, nudge by a frame and discard drafts. [Video review](docs/VIDEO_REVIEW.md).
- **Shorts:** compare opening/ending and source narration, inspect caption chunks and measured/estimated pacing. [Shorts review](docs/SHORTS_REVIEW.md).

AI claims are not automatically verified facts. Structural JSON checks cannot prove story accuracy; review the images and listen before publishing.

## Voices and internet access

New long-form selections use **Gemini Charon at 1.2×**, with **Brian as an explicit backup**. New Brian recordings and Shorts projects also default to 1.2× with 180 ms period pauses. Existing jobs, projects and recordings retain their saved settings.

Gemini receives narration through its API; narration writing uses your external AI chat. Brian speech uses Microsoft's online Edge service. These need internet. Downloading chapters and initially installing models also need internet. Detection, editing, forced timing and video creation run locally once their dependencies are installed.

The app uses free tools and does not enable billing. Provider quotas, free-tier eligibility and website availability are controlled by those services. Gemini audio needs your own ignored `.env` key configuration; see [setup, quotas and recovery](docs/GEMINI_AUDIO.md). The app never silently switches providers or retries ambiguous speech requests.

## Where your work lives

| Folder | Contents |
| --- | --- |
| `manga/` | Original downloaded chapter images. |
| `data/` | JSON narration, memory, projects, queues, checkpoints and diagnostics. |
| `outputs/` | Detections, crops, recordings, timings and media revisions. |
| `outputs/Audio/<manga name>/` | Readable full tracks and individual clips, directly inside each manga folder. |
| `outputs/Videos/<manga name>/` | Readable chapter videos and stitched videos. |
| `outputs/Shorts/` | Separate Shorts projects' media. |
| `backups/` | Verified backups and restore safety copies. |
| `vendor/` | Repo-local model files. |

Use the always-visible **Open saved audio/videos folder** buttons for Finder/Explorer. **Create video** makes a file locally; **Save video to computer** downloads another copy to your browser's chosen destination.

Manga, narration, keys, local data, generated media, models, caches, backups and virtual environments stay out of Git. A new clone contains code, not your Mac's saved work. Use [Backup & storage](docs/BACKUP_RESTORE.md) or follow the [Windows transfer instructions](docs/WINDOWS_SETUP.md) when moving work; recreate the virtual environment for each OS. Backups exclude API keys and model weights.

## When something fails

Read the action, affected file, cause, recovery steps and diagnostic reference shown by the app. Completed work and older revisions remain saved; do not delete state to hide an error. [Error and recovery guide](docs/ERROR_HANDLING.md).

## Development

Run the complete local regression suite with one command:

```sh
.venv/bin/python -m scripts.run_tests
```

Windows PowerShell: `.\.venv\Scripts\python.exe -X utf8 -m scripts.run_tests`.

Tests require Node.js, FFmpeg and ffprobe in addition to the Python environment. They use synthetic fixtures and mocked online/model responses, including short local media tests. They do not process your manga or synthesize real speech. See [development and testing](docs/DEVELOPMENT.md) and the [architecture map](docs/ARCHITECTURE.md).

Historical `/library` routes and older stage-named modules remain for saved-work compatibility. The numbered sidebar is the current workflow; old module numbers do not necessarily match today's stage numbers.
