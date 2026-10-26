# Long-term story memory

In **Stage 5 → Narration**, choose a manga and open **Long-term story memory**, directly below Chapter selection. No AI calls or media generation happen here.

1. Find an important fact or unresolved question; filter by AI claims, verified facts, pinned items or thread status.
2. Tick **Pin in future prompts** to protect it from ordinary relevance/recency selection.
3. Tick **Verified by me** only after checking a fact against the manga. AI “confirmed” is a confidence claim, never human verification; pinning does not verify it.
4. Mark a question **Resolved** after the story answers it. Untick to reopen it. This records your review, without inventing a written answer.
5. Press that item's **Save review**, then copy a fresh batch prompt. Save each item's review before changing filters or manga. Reload replaces unsaved checkbox edits.

Every imported fact shows its original chapter, batch and panel evidence. Threads show the original chapter/batch continuity source: the AI did not provide panel evidence for those questions. These controls apply to the rebuilt Stage 5 workflow, independently of the historical Library memory editor.

## What carries forward

The existing context packet remains capped at 18,000 characters, excluding the master prompt and current images/ID manifest. Every eligible active pin is included; pinning is rejected if the total pin allowance would be exceeded, rather than silently dropping existing pins. Resolved pins are dormant until reopened. Other reviewed facts and unresolved questions share bounded space with cast, summaries and AI claims. The packet reports how many unresolved questions were saved versus included. Pin a question to guarantee its inclusion when there are many.

Questions accumulate from all preceding valid batches, not just the latest summary. Repeated identical questions (ignoring case and repeated whitespace) share the original source and resolution state. Differently worded questions remain separate. Original questions remain saved even after resolution or omission from a prompt. The system does not infer that an absent question has been answered.

Only preceding valid sources are eligible. A pin from chapter 10 cannot enter a chapter 2 prompt. Changed claim text, evidence or panel fingerprints cannot inherit a prior verification. Stale sources are labeled and cannot be newly reviewed until refreshed. The app cannot independently prove the AI's prose or a user's verification.

## Safety and storage

Reviews and their change history are written atomically to ignored `data/manga/<id>/story-memory-review.json`. The imported `story-stage5.json`, original images, script, audio, timing and video files remain unchanged. Concurrent saves use revision checks; failures leave existing reviews intact. A damaged review file reports an error rather than resetting memory.

Review controls affect freshly copied prompts. They do not invalidate already accepted narration or rewrite existing chapters, and do not change the receipt schema. If you want an existing script to reflect a review, explicitly import an updated reply for its batch through the usual narration process. Editing the underlying imported narration or panels still uses the existing dependency freshness checks.
