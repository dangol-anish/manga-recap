"""Run Python regressions and all JavaScript control tests on macOS or Windows.

Uses installed tools only. No app startup, model downloads or live TTS calls.
The regression suite uses synthetic fixtures, including local FFmpeg media tests.
"""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    tools = {name: shutil.which(name) for name in ('node', 'ffmpeg', 'ffprobe')}
    missing = [name for name, path in tools.items() if path is None]
    if missing:
        print('Cannot run the complete test suite. Missing tools: ' + ', '.join(missing))
        print('Install the development prerequisites in docs/DEVELOPMENT.md, then retry.')
        print('No tests, app jobs or generation were started.')
        return 1

    checks = [('Python regressions', [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'])]
    checks.extend(
        (path.name, [tools['node'], str(path)])
        for path in sorted((ROOT / 'tests').glob('test_*.cjs'))
    )
    failures = []
    for label, command in checks:
        print(f'Running {label}', flush=True)
        try:
            result = subprocess.run(command, cwd=ROOT, check=False)
            if result.returncode:
                failures.append(f'{label} (exit {result.returncode})')
        except OSError as exc:
            failures.append(f'{label}: {exc}')

    if failures:
        print('Failed checks:\n' + '\n'.join(failures))
        return 1
    print(f'All checks passed: Python regressions and {len(checks) - 1} JavaScript test scripts.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\nTest run stopped by user. No app processing was started.')
        raise SystemExit(130)
