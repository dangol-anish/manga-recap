# Run on macOS

Use a writable local clone, Python 3.11 and FFmpeg/ffprobe. Do not reuse a Windows virtual environment. No Codex session is needed for daily use.

1. Install Python 3.11, Git and FFmpeg with your preferred package manager. Both `ffmpeg` and `ffprobe` must be on PATH. Use FFmpeg 7 or newer with libx264, AAC and libmp3lame; the installation check verifies actual media support.
2. Clone the repository and enter it:

```sh
git clone --branch main https://github.com/dangol-anish/manga-recap.git
cd manga-recap
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip check
```

3. Install and validate the pinned Magi model. This downloads model files into this clone; validation uses a synthetic image, never a manga chapter:

```sh
.venv/bin/python -m scripts.install_model
.venv/bin/python -m scripts.validate_model
.venv/bin/python -m scripts.doctor --media
```

Stage 3 requires successful model validation. The pinned model and native libraries take space and memory; setup success does not guarantee model performance on every laptop. If installation fails, read the exact command output rather than continuing with a partial environment.

4. Configure [Gemini audio](GEMINI_AUDIO.md) in this clone's `.env` if you want Gemini speech. No key is needed simply to open the app. The free English forced-timing model downloads on first use; saved models remain under ignored `vendor/`.
5. Start the app:

```sh
./run.sh
```

Open [localhost](http://127.0.0.1:8000/) in your external browser. If the script lacks executable permission, use `sh run.sh`. Keep its terminal open while processing; press Ctrl+C for safe shutdown. Never start a second instance on port 8000. [Workflow guide](WORKFLOW.md).

For an existing clone, preserve `manga/`, `data/`, `outputs/`, `backups/` and `.env`; do not run cleanup or delete them as part of updating code. Use [backup and restore](BACKUP_RESTORE.md) before moving or replacing saved work. The current development changes appear on GitHub only after an explicit commit and push.
