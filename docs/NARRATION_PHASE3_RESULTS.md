# Narration Phase 3: recovery results

Scope: Stage 5 failure recovery, retry, resume and stop. Synthetic fixtures and mocked browser/provider responses only. No personal browser windows were operated, production queues resumed, app server restarted, extension installed/reloaded, real chapters processed or Gemini requests sent.

## Recovery behavior

- A completed format-invalid answer is saved before repair. At most two format/validation repairs are attempted in the same chat. Exhaustion leaves a paused queue, saved drafts and an explicit fresh-chat retry action.
- A completed refusal or provider-error object is retained and pauses without an automatic repair request.
- A completed rejected batch uses Retry this batch in a fresh chat. That creates a new attempt directory, clears only its request checkpoints, and rechecks source/context freshness. Earlier accepted batches are skipped and unchanged; rejected drafts remain available.
- Resume recovers an uncertain submission or a saved reply rather than automatically sending the batch again. Explicit resubmission remains separate from ordinary Resume.
- Stop cancels local work and keeps saved replies/imports. It does not promise to cancel a request already accepted by Google or close personal tabs. Normal stopping is reported as stopping, not as a drive/storage failure.
- Resume is rejected while a queue is already running or its prior worker is still stopping. This prevents a resumed job from being immediately terminated by the old worker's cancellation signal.
- Stop reconciles a running/stopping checkpoint even if no worker remains. Completed queue state remains unchanged.
- Corrupt/missing reply files pause without sending another request. Source/context changes still block recovery. App startup remains read-only with respect to browser/provider work.
- Stop does not require an extension connection. Retry/Resume do. Rejected-response controls show the rejection reason and distinguish fresh retry from uncertain-submission recovery.

## Evidence

83 focused Python tests passed across browser narration, extension transport, response handling, narration validation, errors, controls and documentation. Two JavaScript scripts passed: browser narration UI and existing Stage 5 manual UI. Python compilation and git diff whitespace checks passed.

New failure tests cover empty output, acknowledgements, malformed JSON, wrong JSON root, shared two-repair limit, full-format repair to a valid answer, retained drafts, refusal with no automatic repair, retrying only Batch 2 while preserving accepted Batch 1, stopping during reply wait, rejecting Resume during shutdown, missing saved reply without resubmission, and reconciling an orphan stopping checkpoint. Existing tests cover ambiguous sends, explicit retry, quota pause/resume, stale-source blocking, saved-reply import recovery, checkpoint failure and startup recovery. UI tests confirm the fresh retry action and disconnected Stop behavior.

Python log: /tmp/phase3-final.log.

This is synthetic evidence, not a claim of live AI Studio reliability. Deployment and a supervised live pilot remain separate. Progress redesign and queue clear/history are not part of Phase 3.
