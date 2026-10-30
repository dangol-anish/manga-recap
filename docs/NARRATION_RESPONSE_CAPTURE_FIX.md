# Response capture and completion recovery — 2026-10-30

The live page showed a completed model response with feedback controls, a collapsed JSON code viewer and Response ready. The queue had paused in awaiting_reply with a new_chat checkpoint. Inspection used only the Narration/AI Studio/Extensions window on the user's current screen.

Changes:
- The reader expands the latest model response's collapsed code viewer before capture. It scopes this to a model turn containing a Good response control, never a prompt formatting example.
- Code containers using display:contents and collapsed descendants are read from the identified model turn rather than discarded solely for having no layout rectangle.
- A completed model turn's feedback marker is sufficient positive completion evidence even if the Run control is unavailable. Active generation still prevents capture and the backend still requires three stable snapshots before returning output.
- An unrecognized transient page state now has a two-minute grace period, replacing the premature 15-second pause. The 20-minute generation timeout and no-resend behavior remain.
- When the initial checkpoint is still /prompts/new_chat, recovery inspects the already-paired AI Studio tab instead of navigating away. It validates that tab's origin/path, and the normal receipt/source checks still gate import.

Tests: 41 focused Python tests, the real unpacked-extension synthetic fixture (including collapsed code, display:contents, missing Run and rejection safety), the schema comparison test and syntax/whitespace checks passed. Extension version 0.5.2 was reloaded; the app was restarted with the backend fixes and paired again.

Live verification: Resume recovered the previously completed Batch 1 from the existing tab, validated it and saved it without another submission. The queue automatically advanced to Batch 2, configured its schema, inserted the prompt, attached labeled photos and submitted; native UI observation showed Model / Thinking, Stop and Running during generation. Further results recorded below.

Local page-save diagnostic files contain only initial HTML and are not used as a response fixture or as evidence of the rendered DOM.

Final live result: Batch 2 completed, was automatically expanded/read, passed exact narration validation and saved with correction=0. The queue reached 2/33 saved and advanced to Batch 3 preparation. Stop was requested immediately; final state is paused, phase ready, cursor 2. Both raw capture checkpoints are retained (9,325 and 8,560 characters), finished=true. No third-batch reply exists and its ready phase confirms it was stopped before submission. The Narration page was refreshed and visibly shows 2/33 batches saved, paused, with Resume available.

Notification cleanup also removes an earlier attention toast when the queue resumes and uses the concise cause rather than repeating the expanded diagnostic message. Existing queue UI checks passed. The page now loads browser_narration.js v7. No commits or pushes.
