# Narration Phase 2 results

Scope: reply capture, completion detection, schema persistence and tab navigation. Implementation and isolated tests only. No personal browser window was operated, no app server restarted, no installed extension reloaded, and no real chapters or Gemini requests were run.

## Changes

- The active extension contract now sends identified model turns, not full conversation text. Prompt examples are excluded. Duplicate-text answers remain distinguishable by turn identity. Legacy snapshots are rejected instead of silently falling back to whole-page parsing.
- Completed output is captured regardless of whether it is valid JSON. Raw model output is retained separately from strict transport-envelope decoding. Duplicate keys, malformed JSON and invalid structures are not silently repaired or imported.
- Generation state is distinct from completion. A disabled Run button does not prevent recognizing completion. Streaming output is not accepted. An unreadable completed reply or unrecognizable idle page pauses after a bounded 15-second observation instead of waiting for the 20-minute generation timeout. Actual active generation retains its separate timeout.
- Schema editing uses native edit events, then reopens the saved editor and compares its parsed contents. Whitespace/object-key order do not matter. A schema which did not persist blocks submission.
- Navigation requires the requested address, a completed load and a new content document before preparation. Navigation failure/redirect, expired commands and closed tabs fail without submitting or recreating arbitrary personal tabs. The explicit paired-tab scope is retained.
- Existing unfinished queue-contract changes were aligned with tests: full versus partial correction modes and the new extension protocol handshake.

## Evidence

Focused Python regression suite: tests.test_studio_responses, tests.test_browser_narration, tests.test_narration_extension, tests.test_controls, tests.test_documentation.

Unpacked-extension test: actual extension loaded in a temporary Chromium profile, with synthetic AI Studio documents and a local fixture transport. All Google document traffic is intercepted with fixture responses; other external page traffic is blocked. Tests cover ordered photos, repeat fresh chats, saved schema, rejected schema, prompt-example exclusion, empty/plain/refusal/malformed/wrapped/wrong-root output, generation versus completion with disabled Run, expiry, navigation redirect failure, successful recovery, provider usage-limit notice and a closed paired tab.

JavaScript queue UI regression also passes. Syntax and diff whitespace checks pass.

Logs: /tmp/phase2-final-python.log, /tmp/phase2-browser-final.log.

## Deliberately not claimed

This does not prove compatibility with the current live AI Studio DOM or guarantee uninterrupted generation. That requires a separately supervised live pilot after installing the tested build. No production installation or real generation was performed in Phase 2. Progress redesign, queue history/cleanup and Windows verification remain later work.
