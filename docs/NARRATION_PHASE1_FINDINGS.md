# Stage 5 narration: Phase 1 findings

Scope: code inspection, existing saved queue/replies and isolated local tests only. No browser windows operated, app restarted, queue resumed or Gemini requests sent. No implementation changes in this phase.

## Confirmed failures

1. The legacy reader searches the entire conversation for objects containing `beats`. The current saved rejected draft contains the prompt example's empty `batch_receipt`, not a valid source receipt. Prompt text can be mistaken for output.
2. Filtering only for objects with `beats` discards completed empty output, acknowledgements, prose and malformed JSON before validation. This explains completed replies appearing stuck instead of receiving a direct response error.
3. Schema persistence was not established in the live pilot: saved notes and screenshots show the default `response:string` schema despite programmatic fills. Current source now contains native editing and read-back checks, but these changes are unfinished and unverified against the live editor.
4. Source and tests are out of sync. Of 23 focused Python tests, 21 pass and 2 fail. The correction fixture does not support the new `reply_mode` argument; the adapter fixture does not supply the new protocol handshake. These are test-contract failures, not proof that the corresponding live paths succeed or fail. The browser fixture also still expects the old `text` snapshot and lacks controls now required for schema read-back.
5. Tab selection is remembered using a browser tab ID, while navigation waits for a new document. There is no complete end-to-end fixture proving failed navigation, closed/replaced tabs, delayed loads and recovery together. This remains a verification gap, not a confirmed explanation for every reported navigation failure.
6. Pairing tokens exist only in server memory. Restarting the app invalidates the extension connection. Idle polling updates connection freshness, but long browser actions do not send independent heartbeats and can appear disconnected after 15 seconds.
7. Progress exposes coarse queue phases. It does not report individual navigation, schema, attachment or generation activity with phase elapsed time. A live browser action can therefore look idle.
8. Earlier button feedback was missing or distant from its controls. Current frontend additions improve this, and its isolated JavaScript test passes. Actual responsiveness across the whole narration page is not established by that test.
9. Queue clear/hide/history controls are absent. Records remain visible after failure or completion.

## Saved work observed

The current disk checkpoint is paused at `reply_saved` with a validation failure. One batch is accepted. Its rejected draft includes an empty example receipt. No saved work was changed during the audit.

## Phase 2 entry criteria

First align the reader/extension/test contracts and replace whole-conversation extraction with verified assistant-only capture. Prove that every terminal reply reaches strict validation or an explicit bounded failure, with raw output retained. Verify schema read-back and navigation before any submit. Test one synthetic batch across these boundaries before any supervised real batch. Queue UI redesign, history cleanup and Windows verification remain later phases.
