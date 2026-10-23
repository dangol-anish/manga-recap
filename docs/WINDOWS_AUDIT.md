# Windows compatibility audit — 2026-10-23

## Result and verification boundary

Confirmed compatibility problems were fixed, and the existing pipeline remains intact. The local regression suite and the installation media check pass on macOS. Windows x64/Python 3.11 wheels resolve for the pinned dependencies. **Native Windows execution has not occurred in this session**: the setup batch file, Windows APIs, NTFS behavior, actual CPU inference and online services still need the target Windows machine or the new Windows CI job. This is not a first-run guarantee based solely on Mac tests.

Use [WINDOWS_SETUP.md](WINDOWS_SETUP.md) for installation, daily use, transfer of existing work and troubleshooting. No commit, push, live chapter detection, online TTS or real chapter render was performed during this audit.

## Scope

The review covered the repository-owned runtime and entry points: 33 existing Python runtime modules, nine existing helper scripts, 49 frontend HTML/CSS/JavaScript files, launchers, dependency pins, storage rules and the existing test harness. New setup/diagnostic code and regression tests were reviewed with them. Checks focused on Windows-specific assumptions and their effect on each stage, including the historical routes still present for compatibility.

Areas examined: Python/ABI/wheel availability; CPU model loading and local caches; subprocess arguments and command length; encoding; native Explorer/Finder opening; repo-relative paths and filename restrictions; hard links, atomic commits and file locks; job exclusivity, cancellation/recovery and shutdown; image geometry/cropping/labels; narration receipts/context/anchors; TTS settings, timestamps and PCM assembly; FFmpeg filters/codecs/timing and stream-copy stitching; native media seeking/HTTP ranges; shared controls and browser APIs; clean-checkout startup and ignored data. Third-party library binaries/model weights are not claimed to have been audited line by line.

## Fixed findings

| Impact | Before | Change and evidence |
| --- | --- | --- |
| Full chapters could fail to render on Windows | The entire animation/transition filter graph was passed as one process argument. A 116-panel graph exceeds Windows' 32,767-character process command limit. | `app/video.py` writes UTF-8 `visual.ffscript` and uses FFmpeg's file-valued `-/filter_complex` option. A full-chapter fixture parses the large graph successfully; another test enforces a small command line. The real short media fixture exercises the same rendering path. FFmpeg 7+ is required; the obsolete `-filter_complex_script` option is not used. |
| Non-ASCII narration, names or prompts could fail or decode incorrectly | Several JSON, master-prompt, generated-prompt, log/progress and ffprobe reads/writes depended on Windows' system code page. | Text I/O explicitly uses UTF-8, FFprobe text decoding is explicit, and `run.bat` enables UTF-8 mode for the server and inherited workers. Raw UTF-8 Japanese/accented JSON and non-ASCII media paths have fixtures. JSON digest algorithms/content were preserved. |
| Windows could sleep in the middle of detection | The keep-awake context only used macOS `caffeinate`. | Shared `app/runtime.py` uses `SetThreadExecutionState` on Windows and restores it on exit/error. Failure emits a clear warning. Mocked Windows branches check request/release/failure; native Windows behavior remains for the Windows run. Lid-close/shutdown policies are not overridden. |
| Legacy processing could not start on Windows | An older Library endpoint launched `.venv/bin/python`. | It now uses `sys.executable`, as current Stage 3 already did. The installation error gives platform-neutral module instructions. Legacy workers hold an OS lock; a surviving Windows legacy worker blocks conflicting recovery rather than allowing two writers. Current Stage 3 retains its existing worker-lock/stop-marker recovery. |
| Valid input could produce illegal Windows chapter folder names | Fallback chapter names could be `CON`, `AUX`, `NUL`, `COM1`, etc., or end with dots/spaces after truncation. | `chapter_names.py` sanitizes these fallbacks. Normal names such as `Chapter 2.5` are unchanged. Tests cover reserved basenames, suffixes and decimal chapters. Existing folders were not renamed. |
| Transferring workspace state between operating systems could lose paths | Some new persisted relative paths used `str(Path)`, producing backslashes on Windows. | New saved paths in Library registration, Stitch and migration/report records use `.as_posix()`. Native OS paths are still passed to subprocesses/Explorer. Main pipeline IDs, receipts and existing source fingerprints are unchanged. |
| Short Windows sharing conflicts could fail JSON checkpoints | An atomic replacement could fail when another reader or scanner briefly held a Windows handle denying deletion. | `storage.write_json` retries only Windows sharing/access error codes, with a bounded delay. Persistent failure preserves the previous committed JSON and removes the temporary file. Tests cover both transient and persistent failures. |
| Windows source validation missed a mid-read change check | The Windows uncached hash path read fresh content but omitted the post-read stat check used by the cached branch. | Both paths now verify before/after stamps. The Windows branch retains its deliberate avoidance of cross-request caching based on creation time. Tests simulate a changing source. |
| Partial/corrupt model installs could be mistaken for a usable install | Any nonempty existing model file was skipped, regardless of expected size/hash; downloads lacked integrity checks/retries. | The installer obtains the pinned manifest, checks the revision, validates LFS SHA-256 or Git blob hashes and size, retries downloads and publishes only verified files. Old files survive failed replacement attempts. Nested paths/symlinks/path escapes are checked. A source-only mode supports CI without weights. Mocked failure/retry/path fixtures pass; all 16 existing model files were independently verified against the live pinned official manifest without changing them. |
| Test suite depended on the development machine | Some fixture temp directories required ignored `outputs/` to exist; symlink fixtures required Windows privilege; cache call-count assertions assumed the Mac branch. | Fixtures now work from a clean checkout, symlink tests explicitly skip only Windows privilege error 1314, and cache assertions account for the intentional Windows behavior. Other filesystem errors still fail. Fresh-start tests load all eight current pages/catalogs with no manga or outputs. |
| Fresh Windows setup was underspecified | No complete Windows installation path/preflight; README mixed old ZIP/Library steps with the rebuilt workflow. | Added `setup-windows.bat`, `scripts.doctor`, a detailed Windows guide, current daily workflow and a Windows/macOS CI matrix. Windows-only `colorama` is pinned. `.gitattributes` keeps shell scripts LF and batch launchers CRLF. |

## Stage-by-stage review

| Stage | Windows path checked | Result / limits |
| --- | --- | --- |
| 1 Downloader | HTTPX/asyncio workers, source adapters, verified image decode, stop/retry, ownership markers, destination naming, native folder opening | Uses repository-relative inputs and argument-based Explorer opening; mocked network/real image fixtures pass. Live website availability is external and was not retested against real chapters. |
| 2 Renumbering | Natural order, extension preservation, collision-safe two-phase staging, hard-link/unlink recovery | Works through the same code on APFS/NTFS; requires hard-link support. No overwrite fallback is introduced for exFAT. Recovery/collision/source-content tests pass. |
| 3 Detection | Child executable, exclusive worker lock, sequential jobs across manga, cached fingerprints, normal-model panel filtering, Windows keep-awake and sorter watchdog | Current launch already used `sys.executable`; Windows sleep handling is now present. Windows chooses CPU/float32, while supported Macs may use MPS/float16. Runtime/performance and numerically identical boxes across devices are not promised. A Windows ordering watchdog may terminate the isolated worker on a pathological timeout; checkpoints survive and no partial page is committed. |
| 4 Panel Review | Pillow font/image operations, padded crops, revision publication, ID labels, save rollback and independent edits | Uses bundled Pillow font support rather than a Mac font path. All current assets are revisioned; synthetic crop/label/editor fixtures pass. Source images and manual edits were not touched. |
| 5 Narration | UTF-8 JSON/prompts, exact IDs/receipts/context hashes, folder export, copyable text, validation/corrections, bounded preceding memory | Current prompt loading was already UTF-8; shared and older paths are now explicit too. No API keys or automatic AI calls added. Browser clipboard has a manual-copy fallback with Windows Ctrl+C guidance. Story factual correctness remains distinct from structural JSON validation. |
| 6 Audio | Edge websocket/asyncio, MP3 decode/encode, timing words/pauses, per-beat revisions, full PCM assembly, queues and readable files | FFmpeg/native imports and mocked speech/real audio fixtures pass. Brian/default speed and all existing chapter profiles/audio were preserved. Live Edge service access requires a Windows target check and internet. |
| 7 Video | Phrase cuts/frame rounding, full filter graph, process cancellation, progress reads, native MP4/range playback, source/timing invalidation and retained exports | Long-command failure fixed. Synthetic real encodes exercise smooth motion, crossfades, timestamps/durations, source staleness and failed publish preservation. Rebuilds do not invoke TTS. File held open by an external Windows player can still require closing that player before retrying. |
| 8 Stitch | Readable selection paths, compatible-stream checks, hard-link/copy staging, relative concat list, FFprobe Unicode, stop/retry and collision-safe publication | Real short-fixture stitching verifies order, duration, unchanged decoded pictures and audio packet payloads, HTTP range playback and source preservation. No real chapter videos were combined. |

All main browser pages continue to use relative HTTP URLs, native HTML audio/video controls and ordinary DOM/canvas APIs, without a Mac-only browser filesystem API. No redesign or browser automation was needed; no web app was opened in the Codex browser.

## Verification performed

- Full Python fixture suite: **279 tests passed** on macOS/Python 3.11; real inference and live speech are mocked, while relevant FFmpeg tests execute real encodes/decodes/stitching.
- JavaScript: **10 control suites passed** using Node 22.
- Installation doctor: pinned packages/native imports, Torch/torchvision/NumPy interoperability, filesystem write/lock/hard links/atomic JSON, MP3 encode/decode and a real one-second MP4 passed. The media fixture path includes spaces, an apostrophe and `é`.
- Windows wheel dry resolution: **67 packages resolved**, including **24 Windows x64 native wheels** and CPU Torch/torchvision; the rest were platform-independent wheels. No Mac/Linux native wheel appeared. This is a cross-target wheel check, not a native Windows import test; host-dependent environment markers are not a substitute for the Windows CI run. Windows `colorama` is explicitly pinned separately.
- Official model: **16 existing files verified** against the live pinned manifest; no download/overwrite or inference was needed for that verification.
- `pip check`, Python compilation and `git diff --check` passed.
- New regression coverage includes Windows API branches, cross-process locking, transient sharing failures, Unicode, portable names, a large graph/small command, clean-checkout startup and installer corruption/retry/path safety.

The GitHub Actions matrix is supplied at `.github/workflows/compatibility.yml`. It installs dependencies and FFmpeg, fetches verified model helper source **without weights**, runs the media doctor and all Python/JS fixtures on Windows and macOS. **It has not been pushed or executed yet.** Full real model validation remains an explicit setup step on the target laptop.

## Remaining deployment conditions

1. Use a short writable local NTFS checkout and Python 3.11 x64. Dependencies on 32-bit/native ARM64/newest Python were not certified. Windows 11 x64 is the intended target; older Windows editions are not independently tested.
2. Install the x64 Visual C++ runtime and a current FFmpeg build with the required codecs/filters. Run setup until it says “Setup passed”; a successful code audit does not supply missing system libraries.
3. CPU Magi needs an actual target-laptop performance/memory trial. Only detection currently requests idle-sleep prevention; keep long audio/video jobs awake and plugged in.
4. The downloader sites and online Edge TTS can fail independently of operating system. The doctor intentionally does not make speech requests or fetch real manga.
5. Windows filename/path rules and file sharing differ from macOS. Existing Mac work with illegal Windows names or excessive paths needs a deliberate migration. Saved user assets were not renamed/deleted by this audit.
6. Stop server/workers before moving the workspace or pulling code. Keep `manga`, `data` and `outputs` together; recreate `.venv` per OS. No generated media is part of the source change.

## Primary references

- [Microsoft process command-line limit](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw)
- [FFmpeg's file-valued option syntax (7.0)](https://github.com/FFmpeg/FFmpeg/blob/n7.0/doc/fftools-common-opts.texi)
- [PyTorch 2.6 installation commands](https://docs.pytorch.org/get-started/previous-versions/)
- [Python on Windows: UTF-8 mode, launcher and long paths](https://docs.python.org/3.11/using/windows.html)
- [Microsoft Visual C++ runtime](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)
- [Official pinned Magi v3 source](https://huggingface.co/ragavsachdeva/magiv3/tree/49c73a225122d53adbaa26d53868be81a57706e2)
