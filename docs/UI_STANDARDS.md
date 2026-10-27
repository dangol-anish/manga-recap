# Shared UI controls and spacing

Every HTML page, including retained compatibility views, uses the approved dark palette and loads these sheets **after page-specific styles**, in this order:

1. `app/static/layout.css`: palette, page/card rhythm, responsive spacing and consistent heading hierarchy.
2. `app/static/controls.css`: full native control appearance and geometry, not merely the dropdown arrow.

Single-line dropdowns are native `<select>` elements: 44px high, 13px text, 8px corners, a 1px border, 13px leading padding, 44px trailing text space and a 14px inset arrow. Their dark surface, options, disabled state and keyboard focus are shared. Dynamic selects inherit the same rules without needing a page-specific class. Multi-select/listboxes are not coerced into this single-line geometry.

Text inputs and textareas share typography, border and padding. Checkboxes/radios keep native behavior and 16px sizing; they never inherit a text field's minimum height. Buttons use a 44px minimum target. Labels have an 8px field gap; normal content/card spacing uses the shared 8/12/16/24px rhythm. Page-specific grids may define widths and columns, but must not redefine the shared dropdown arrow or control appearance. Avoid inline control styles.

At narrow widths, fields stay within their container and action groups wrap/stack. Native keyboard interaction remains available. Forced-colors mode uses the operating system's select arrow and colors; reduced transparency removes blur. The expanded native option popup can retain OS-specific presentation, while the closed field has consistent app styling.

## Automated browser checks

The app itself does **not** require Playwright or test browser downloads. For development only:

```sh
.venv/bin/python -m pip install -r requirements-ui.txt
PLAYWRIGHT_BROWSERS_PATH=.cache/ui-browsers .venv/bin/python -m playwright install chromium firefox webkit
.venv/bin/python -m scripts.check_ui --browsers chromium firefox webkit --screenshots
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ui.txt
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD/.cache/ui-browsers"
.\.venv\Scripts\python.exe -m playwright install chromium firefox
.\.venv\Scripts\python.exe -m scripts.check_ui --browsers chromium firefox --screenshots
```

The checker starts its own ephemeral loopback fixture server. It renders the actual HTML and CSS of all 17 pages, strips application scripts, blocks app APIs and serves no user manga/media. Representative dynamic progress, audio-review and Shorts fields are included. Desktop (1280px), tablet (768px) and phone (390px) layouts are checked for control size, padding, arrow inset, text, clipping, horizontal overflow, button targets, checkbox isolation, native selection/focus and forced-colors fallback. Screenshots and JSON reports go under ignored `reports/ui/`; browser binaries stay in ignored `.cache/ui-browsers/`.

The normal `scripts.run_tests` suite checks that every page loads the shared sheets last and that fixture scripts cannot start app processing. Windows/macOS CI additionally installs development browsers and runs the rendered checks, retaining **synthetic UI reports only** for seven days. This checks browser/platform layout; it does not prove live download sites, speech providers or real Magi inference work.

[Playwright browser installation reference](https://playwright.dev/python/docs/browsers).
