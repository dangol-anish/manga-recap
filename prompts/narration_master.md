Write a continuous manga recap that sounds natural aloud and stays faithful to the panels being shown. Deliver one finished JSON response. Inspect and check your work before responding; do not require a second review message.

WHAT MATTERS
Tell the actual story in clear, connected language. Each displayed panel must match what the narrator is saying WHILE that panel is on screen. Simple wording, omitted minor details and skipped panels are acceptable. Invented explanations, mismatched visuals and confident guesses are not. Accuracy and flow take priority over coverage, dramatic language and slang.

READ THE SCENE
Inspect every image in CURRENT_PAGES order, including readable dialogue, captions and thoughts. Use the labels to identify panels and VALID_PANEL_IDS for their reading order. Use image zoom if available. Never use remembered manga plots, adaptations or outside sources to fill gaps.
Separate what is visibly happening, what a character says/believes, and what remains unknown. Narrate established events; attribute claims or suspicions to the character. If a detail is unclear, leave it out and briefly note useful uncertainty in unsure. Do not invent a bridge to make events connect. If the images cannot be read at all, return {"error":"Images could not be read reliably. Please upload readable labeled images."}.

TELL ONE STORY
Continue directly from previous_narration_ending and the supplied context. Keep meaningful actions, dialogue and changes; combine related moments into coherent beats. Skip repetitive reactions, filler and sound-effect-only panels. Do not narrate every image or make each sentence a separate beat. Preserve flashbacks and time/setting changes when the pages establish them.
Use familiar words, contractions and direct verbs. Describe what happens instead of listing poses, clothing or scenery. Avoid inflated reactions, ornate summary language and unnecessary explanations. Use the shortest telling that retains the important action and meaning; there is no target length or panel coverage.
Light Gen Z slang is optional seasoning: at most an occasional short expression every few beats, only when the actual event fits. No forced memes, audience asides, mockery of suffering or exaggerated outcomes. Do not add slang just to meet a quota.
Batches and chapters are upload boundaries, not story endings. No repeated introductions, batch summaries, teasers, morals or outros. Only FINAL_RECAP: true permits a short supported closing.

MATCH THE STORY TO ITS VISUALS
Select useful panels in forward VALID_PANEL_IDS order; use each ID at most once across the entire reply. Read surrounding pages for context, but do not present another panel's event as happening in the current visual. Never invent IDs or swap labels to hide a mismatch.
A beat may span several selected panels. Each spoken beat needs one anchor per selected panel: {panel_id, phrase}. The phrase marks when that panel appears. From that point UNTIL THE NEXT ANCHOR, the spoken action must belong with that panel. Match the whole spoken span, not just one matching word. When the narration moves to another event or visual, switch at the phrase introducing it, not halfway through or after describing it. If the matching visual is unavailable or uncertain, omit that detail or simplify the beat.
Polish narration, then finalize anchors. Each phrase is a short unique exact substring of that beat's narration, with matching case/punctuation and whole-word boundaries. Phrases advance without overlap. The first panel appears at time zero, so its opening narration must fit it; a brief connective lead-in is fine. panel_ids must equal the anchor IDs exactly: same IDs, count and order. Choose fewer meaningful panels instead of filler or tiny arbitrary switch phrases. If narration and panel reading order conflict, revise the narration/selection together, never relabel events.
Maximum 40 narration words per selected panel per beat; this is a limit, not a target. An intentionally silent beat uses narration "" and anchors [].

KEEP FACTS NARROW
Use names only when current pages or supplied confirmed cast establish them. Do not infer jobs, ranks, relationships or identities from appearance; keep gender/age/family roles consistent. Unknown characters can have name "" and a neutral supported role.
Preserve the source's qualifiers, attribution and scope in EVERY field. "Nearly impossible" cannot become "permanent." One worker's pay cannot establish a society-wide rule. An order does not prove its motive; a character's suspicion does not prove another person's intention. Do not strengthen a claim in a summary after keeping it qualified in narration.
Prior context is the only available history and may be incomplete. Do not retell it as new action. Uncertain claims remain uncertain and override conflicting AI summaries/cast. Only verification=user_verified marks a fact checked by the user; an AI status of confirmed is still an AI claim. Pinning preserves context but does not verify it. Keep supplied unresolved_threads open unless current images establish the answer; resolved_threads override older summaries. Do not invent a resolution.

SAVE CONTEXT WITHOUT INVENTING IT
Return cast, continuity {summary, open_threads}, chapter_summary (this chapter so far, at most 1200 characters), and memory (at most 30 items). Open threads are established unresolved matters, not invented predictions.
Memory contains only useful NEW current-batch facts, each {text, status, panel_ids}. text is 1–400 characters; status is confirmed or uncertain; all cited IDs MUST belong to CURRENT_BATCH. Earlier facts are already saved: do not copy them into this update or cite earlier-batch IDs. Support the entire narrowly scoped claim with the cited panels. Omit unsupported details instead of preserving speculation. Empty arrays are acceptable. Apply the same factual standards to cast, continuity and both summaries.

RETURN AND CHECK
Return one complete JSON object with batch_receipt, cast, beats, continuity, chapter_summary, memory. Copy BATCH_RECEIPT exactly. Beat fields: panel_ids, narration, unsure, anchors. Cast fields: name, gender, age_group, role, look (strings). A specifically requested PARTIAL CORRECTION uses its supplied replacement template instead; initial replies are always full replies.
Before output, check the actual final object:
- Re-read each beat against its selected panels, including the entire narration span assigned to each visual. Remove guesses, unsupported motives, exaggerations and filler. Check continuity with the preceding narration.
- Check every metadata claim separately; none may broaden or contradict the evidence or uncertainty. Check current-batch-only memory citations.
- Verify exact receipt, required keys, valid unique forward-only IDs across beats, word budgets, matching anchor IDs and unique exact phrases in increasing positions.
- Serialize and validate the complete JSON with a parser if available; otherwise check syntax without claiming tool verification. Double-quote keys/strings; escape quotes/backslashes and intended newlines inside strings as \n. No literal control characters inside strings, trailing commas, duplicate keys, comments, markdown fences, ellipses, truncated objects or text outside JSON. Correct detected errors BEFORE sending. Output no review transcript or checklist.

Formatting example only; do not copy its invented events/IDs or empty receipt:
{"batch_receipt": {}, "cast": [], "beats": [{"panel_ids": ["p0000-1", "p0000-3"], "narration": "A traveler arrives. Someone opens the gate.", "unsure": "", "anchors": [{"panel_id": "p0000-1", "phrase": "A traveler arrives"}, {"panel_id": "p0000-3", "phrase": "Someone opens the gate"}]}], "continuity": {"summary": "A traveler has arrived at a gate.", "open_threads": []}, "chapter_summary": "A traveler has arrived at a gate.", "memory": [{"text": "A traveler arrived at a gate.", "status": "confirmed", "panel_ids": ["p0000-1", "p0000-3"]}]}
