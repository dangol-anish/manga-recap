# Run Manga Recap on Windows

This guide is for **Windows 11 x64 (Intel/AMD), Python 3.11 64-bit, and a local NTFS drive**. Windows 10 x64 uses the same code paths but is not independently tested here. Native ARM64 Python, 32-bit Windows, WSL, network drives and exFAT are outside this setup. You do not need Codex to run the app.

The compatibility fixes and this guide must be present in your checkout: look for `setup-windows.bat`. Changes that have not been pushed from the development Mac will not appear in a new GitHub clone.

## 1. Install four free prerequisites

Open **PowerShell** from the Start menu and run these separately:

```powershell
winget install --id Git.Git --exact --source winget
winget install --id Python.Python.3.11 --exact --source winget --architecture x64
winget install --id Microsoft.VCRedist.2015+.x64 --exact --source winget
winget install --id Gyan.FFmpeg --exact --source winget
```

Read and accept the installers' prompts. The Visual C++ **runtime** is needed for native Python libraries; Visual Studio and its compiler are not required by this wheel-only setup. Package installation may request Windows administrator permission. The app itself runs as your normal user.

**Close PowerShell and open a new PowerShell window** so it sees the new PATH. Check:

```powershell
git --version
py -3.11 -c "import sys, struct; print(sys.version); print('bits:', struct.calcsize('P') * 8)"
ffmpeg -version
ffprobe -version
```

You should see Python **3.11**, **64 bits**, and working `ffmpeg` and `ffprobe`. Use FFmpeg **7 or newer** with H.264/libx264, AAC and libmp3lame; the setup's real media test checks the required filters and codecs. Do not select the newest Python release by default: the project's pinned Torch/torchvision and NumPy versions need a compatible interpreter.

If `winget` is unavailable, install from [Git for Windows](https://git-scm.com/install/windows), [Python's Windows releases](https://www.python.org/downloads/windows/), [Microsoft's x64 Visual C++ runtime](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist), and the [Windows builds linked by FFmpeg](https://ffmpeg.org/download.html). Choose a **Python 3.11 x64 full installer**, include the Python launcher and pip, and enable its long-path option if offered. For a manual FFmpeg install, extract it to a short folder such as `C:\ffmpeg`, add the folder containing **both** executables (usually `C:\ffmpeg\bin`) to your user PATH, and reopen PowerShell. Gyan's essentials/full builds contain the tools this app needs.

## 2. Clone into a short, local path

```powershell
git clone --branch main https://github.com/dangol-anish/manga-recap.git C:\manga-recap
cd C:\manga-recap
```

If GitHub asks, sign in to an account allowed to access the repository. If `C:\manga-recap` is not writable, use another short writable NTFS location, such as `D:\manga-recap`; use that location in subsequent commands. Avoid OneDrive, Program Files, network shares, USB/exFAT drives and deeply nested folders. Python and FFmpeg create long internal asset names; a short clone path reduces Windows path-length problems. Enabling Python's long-path support alone does not make every external program support every long path.

## 3. Run the setup once

From the repository in PowerShell:

```powershell
.\setup-windows.bat
```

Keep the terminal open. This script:

1. Checks Python 3.11 x64 and FFmpeg availability.
2. Creates this clone's `.venv` if needed; it does not modify a global Python environment.
3. Installs **CPU-only Torch 2.6.0 / torchvision 0.21.0**, followed by the pinned dependencies, using prebuilt wheels.
4. Checks package compatibility, native imports, filesystem locking/hard links, UTF-8 JSON, MP3 encoding/decoding and a real one-second synthetic MP4 with the app's motion/transitions.
5. Downloads the official **Magi v3 revision `49c73a225122d53adbaa26d53868be81a57706e2`** into `vendor/magiv3`, verifies file sizes/hashes and retries failed transfers.
6. Validates the installed model on a **synthetic drawing**, enabling Stage 3 only if it passes.

It does not download or process any real manga, contact TTS or regenerate existing media. Installation, wheels and the model require internet. Model weights alone are about **1.67 GB**; allow additional space for packages, caches, images, crops and rendered videos. As a planning recommendation, start with **20 GB or more free** and **16 GB RAM**; smaller machines need an actual trial rather than a promised performance estimate. CPU panel detection can be substantially slower than Apple MPS on the development Mac. An NVIDIA GPU is not required or automatically used.

Success ends with **“Setup passed”**. If a step fails, the script stops instead of launching a half-installed app. Fix the reported cause and run it again. Already verified model files are reused; an interrupted individual model transfer restarts that file, rather than resuming its partial bytes.

No virtual-environment activation or PowerShell execution-policy change is needed. Do not copy the Mac's `.venv` to Windows.

## Configure Gemini audio keys

The complete app, including Shorts and grouped Gemini audio, is available on `main`.
Before using Gemini audio, create `.env` in the repository root:

```powershell
notepad .env
```

Save your own working keys using these variable names (replace the placeholder values):

```dotenv
GEMINI_API_KEY=your_first_key
GEMINI_API_KEY_2=your_second_key
GEMINI_API_KEY_3=your_third_key
```

The file must be named `.env`, not `.env.txt`. It is ignored by Git and is never included in a clone. Additional keys are optional; keys on the same Google project share its quota. Confirm free-tier/billing settings in Google AI Studio. The app does not enable billing. No key is needed to open the app; Gemini generation needs a valid key and internet. See [Gemini audio](GEMINI_AUDIO.md) for quota, recovery and storage details.

## 4. Start the app every time

```powershell
cd C:\manga-recap
.\run.bat
```

Wait for **“Application startup complete”** and the `http://127.0.0.1:8000` address in the terminal. Leave that window running. Open your normal external browser at:

**http://127.0.0.1:8000/**

Or use a second PowerShell window:

```powershell
Start-Process "http://127.0.0.1:8000/"
```

Use a current Edge, Chrome, Firefox or Brave. The app stays on your computer at loopback; no public server, router changes or inbound public access are needed. Run one server instance only; do not add multiple Uvicorn workers or `--reload` while processing.

## 5. Use the stages in order

1. **Downloader:** paste a MangaPill or WeebCentral series URL, load chapters, select chapters and download; **Open folder** opens Explorer.
2. **Renumbering:** manually delete unwanted images in Explorer, then select their chapter folders and renumber; remaining images become `0000`, `0001`, etc., retaining their extensions.
3. **Panel Detection:** select manga/chapters, set each manga's reading direction and start; watch progress, Stop/Resume as needed. Windows idle sleep is prevented while the detection worker runs, but this cannot override shutting down or a lid-close power policy.
4. **Panel Review:** select detected chapters and prepare crops/labeled pages; the existing hidden review contact sheet remains hidden. Export & narrate takes you to Narration.
5. **Narration:** export a batch of at most 20 labeled photos, attach them to a fresh image-capable AI chat, copy the matching prompt, then import the JSON reply into that batch; repeat in order. The app carries preceding saved story context. Only mark the final recap when the full story you are making ends there.
6. **Audio:** choose a manga and select fully imported chapters, then press **Create selected audio**. Gemini groups narration across chapters into sections rather than sending every chapter separately; new selections default to Charon at 1.2×. Listen to the full selected audio. Brian is an explicit backup for remaining sections, not an automatic provider switch. Existing recordings/settings stay intact. **Internet is required:** Gemini sends narration to Google; Brian sends narration to Microsoft. See [Gemini audio](GEMINI_AUDIO.md).
7. **Video:** choose a chapter with current audio, click **Create video**, watch rendering progress, then use the real video player; timing changes require saving and rebuilding. Save/download the completed MP4.
8. **Stitch:** select at least two finished chapter videos from one manga and combine; compatible videos are joined without re-encoding.
9. **Shorts (optional):** bundle selected chapters’ saved narration, copy the prompt/schema into external Gemini chat, import proposals, then explicitly select a proposal for separate vertical audio/video creation. See [Shorts](SHORTS.md).

Start with one short chapter before queuing a large library. There is no automatic real-manga processing on startup. Download sites, the external narration AI and online TTS are services outside the app and can impose limits or change.

## 6. Find your files

All local project files are inside **this clone**:

| What | Location under `C:\manga-recap` |
| --- | --- |
| Downloaded original images | `manga\<manga folder>\Chapter 1\` |
| Saved narration/context, job state | `data\` |
| Readable full audio and beat clips | `outputs\Audio\<manga name>\` |
| Readable finished chapter videos | `outputs\Videos\<manga name>\` |
| Combined videos | Same manga's Videos folder, named `Combined video.mp4` (older copies retained) |
| Internal crops/audio/video revisions | `outputs\<manga-id>\<chapter>\...` |
| Official model | `vendor\magiv3\` |
| Virtual environment | `.venv\` |

The always-visible Audio/Video folder buttons populate/open readable copies named, for example, `Chapter1-Audio.mp3`, `Chapter1-Beat001-Audio.mp3`, `Chapter1-Video.mp4`. Original internal revisions remain in their pipeline folders. Saving an MP4 through the browser creates another copy in your browser's chosen downloads location; it does not replace the app's saved original. These assets and local JSON state are ignored by Git.

## 7. Stop, resume and update safely

For a normal stop, use the stage's **Stop** control if available, wait for it to pause, then press **Ctrl+C once** in the server terminal. Wait for shutdown to complete before closing the window or moving files. Closing only the browser does not stop the server. Windows sleep prevention is currently limited to detection; keep the computer plugged in, awake and its lid open during long audio/video jobs.

After restarting, use the appropriate Resume/retry control. Completed detection pages/clips are verified and reused; an interrupted video render is restarted rather than continuing partial encoded bytes. An interrupted Stitch can be retried. Do not rename source folders, change images or edit JSON while a task is running.

To update, stop the server, back up your local work, then:

```powershell
git pull --ff-only
.\setup-windows.bat
.\run.bat
```

Setup rechecks dependencies/model integrity and synthetic validation. It does not regenerate your chapter media. For **code-only** updates with unchanged dependencies/model, you can run the doctor and restart instead of reinstalling.

## 8. Move your existing Mac work to Windows (optional)

Stop the app and all workers on the Mac first. Back up and copy the complete `manga`, `data` and `outputs` directories into the Windows clone, preserving their relative names. Include hidden markers such as `.renumbering.json` / `.download-owner.json` and the JSON index files in `data`. GitHub does not contain them. Recreate `.venv` with Windows setup; re-install/verify the pinned model. Do not merge copies while either app is running.

The current workflow stores stable repo-relative paths/IDs, so moving the complete workspace preserves associations; absolute diagnostic paths in old logs do not drive new work. Mac-only filenames containing Windows-forbidden characters or paths longer than your tools support need a separate careful migration; do not rename chapter folders with saved work casually. Never copy old lock files as a way to continue a running process. Their presence alone does not mean a process is running: OS file locks are what matters.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `py` not found / no Python 3.11 | Install the full x64 Python 3.11 distribution with launcher; reopen PowerShell. |
| `DLL load failed`, `WinError 126` / `1114` importing Torch | Install/repair Microsoft's x64 Visual C++ runtime, restart if requested, verify 64-bit Python and rerun setup. Run the doctor to identify the failing import. |
| `ffmpeg` / `ffprobe` missing | Ensure both are in PATH; reopen the terminal before restarting the app. `where.exe ffmpeg` shows what will run. |
| Unknown FFmpeg option, missing libx264/libmp3lame or filters | Install a current Gyan essentials/full build; remove older conflicting PATH entries; rerun the media doctor check. |
| `No matching distribution` | Verify Python 3.11 x64 and internet/index access. Use the pinned setup script; do not solve this by changing random package versions. |
| Model download failed / integrity failed | Check disk space and network, rerun setup; verified files are reused. Do not disable TLS verification. |
| Stage 3 disabled | Finish `scripts.install_model`, then run `.\.venv\Scripts\python.exe -m scripts.validate_model`; only a successful synthetic check enables detection. |
| `ERR_CONNECTION_REFUSED` | Keep `run.bat` running; read its terminal error; wait for startup. Opening the browser does not start the server. |
| Port 8000 already in use / server lock error | Stop the earlier server normally. Inspect `Get-NetTCPConnection -LocalPort 8000` if necessary; do not blindly kill unrelated Python processes. |
| Permission denied / file in use | Use a writable local NTFS clone; close an external editor/player holding the affected file and retry. Check Windows Security's blocked-app notice if one appears; do not disable protection globally. |
| Path too long | Use a shorter clone path and enable Win32 long paths through the Python installer/Windows admin policy; retain the whole workspace together. External tools may still have limits. |
| Audio chapter unavailable | All its Narration batches must be imported **and current**; refresh after completing them. |
| Video chapter unavailable | Finish all current spoken audio clips first, then refresh Video. |
| Website 403/429 / changed layout, or Edge speech fails | Check internet/service status and retry later; existing saved work is kept. Windows setup cannot remove provider restrictions. |
| Video will not play on Windows N | Install Microsoft's Media Feature Pack for your Windows edition and use an up-to-date browser; verify the file with the media doctor. |

To recheck the local installation without touching chapters:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.doctor --media --model
```

For developer regression tests after setup (optional; Node 22 is needed only for JavaScript tests):

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.run_tests
```

Tests use synthetic fixtures and mocked model/TTS responses. Some symlink fixtures skip if Windows does not grant symlink privilege; Developer Mode can enable those fixtures and is **not** required to use the app. The [Windows/macOS GitHub Actions workflow](https://github.com/dangol-anish/manga-recap/actions/workflows/compatibility.yml) runs these checks on every push; check the latest run for its native Windows result.

## Verification boundary

The code changes, Windows API branches and real FFmpeg fixtures were checked on the development Mac. Windows x64/Python 3.11 dependency-wheel resolution was checked separately. **An actual Windows install, full Magi inference, online download and Edge TTS still need to pass on the target Windows laptop.** The setup script supplies those local prerequisite/model gates; it cannot guarantee another machine's hardware, permissions or external services before that machine runs them.

Primary references: [PyTorch's pinned 2.6 installation commands](https://docs.pytorch.org/get-started/previous-versions/), [torchvision/Python compatibility](https://github.com/pytorch/vision#installation), [Python on Windows and long paths](https://docs.python.org/3.11/using/windows.html), [official Magi v3](https://huggingface.co/ragavsachdeva/magiv3), [FFmpeg command-line documentation](https://www.ffmpeg.org/ffmpeg.html).

For the shared regression command, test prerequisites and code layout, see [Development guide](DEVELOPMENT.md). Updated daily controls are described in [Workflow](WORKFLOW.md).
