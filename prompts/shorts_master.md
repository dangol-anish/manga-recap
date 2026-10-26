You are selecting and writing standalone manga Shorts from the attached shorts-source.json package. This is a text-only analysis of already saved narration, not permission to invent manga events.

Find up to 8 genuinely strong moments across the selected chapters. A moment may cross chapter boundaries. Return zero proposals if none are worthwhile; do not fill a quota. Prefer a clear reversal, confrontation, reveal, consequence, emotionally meaningful choice or joke with a payoff. Each short needs an immediate truthful hook, minimal setup, escalation and a satisfying turn or payoff. Avoid generic introductions, chapter summaries, empty hype, misleading cliffhangers and "watch part two" endings. No claim that a proposal is guaranteed viral.

Write a NEW concise spoken script per short, roughly 30–120 seconds: 60–280 words total, preferably 90–180. Use natural third-person narration and occasional light modern slang only when it fits. Keep cause and effect clear. Do not distort motives, gender, relationships, outcomes or chronology for excitement. A viewer unfamiliar with the series must understand the short without a lore explanation.

SOURCE RULES
- Use the selected source beats as your evidence. Context/cast can clarify names and prior events but cannot supply unseen action or a payoff absent from the selected narration.
- Preserve uncertainty: omit uncertain claims rather than upgrading them to facts. Never rely on knowledge of the series or outside sources.
- You have NOT seen the images. Select only panel IDs explicitly listed in the referenced beat. Do not invent visual details, composition or actions that the text does not establish. These are proposed image choices pending visual review.
- Every segment names its exact chapter, source_beat number and ONE panel_id from that beat. Panel IDs repeat across chapters: chapter is mandatory. Keep segments in source chapter/beat/panel order. You may omit unnecessary beats/panels. A source panel can be held across consecutive segments if needed.
- Most segments should be short spoken phrases of 4–12 words, so panels switch quickly with the action. Allow a longer hold only when a reveal needs it; do not pad with filler or swap to unrelated panels for speed. Each segment contains 1–40 spoken words for THAT panel. Its narration is spoken while that panel is displayed. Start the first segment with the hook itself. Combine the segments into one continuous story, without repeated setup or disjointed panel descriptions.
- The app generates separate audio for these spoken segments and cuts to their panels. Do not return phrase anchors, timestamps, editing commands, crop coordinates, invented IDs or long-form audio references.

OUTPUT
Return exactly one JSON object, with no Markdown or explanation:
{"receipt":{"project_id":"copy provided value","source_token":"copy provided value"},"shorts":[{"title":"Short readable title","why_it_works":"Explain the actual hook and payoff and identify any context limitation; no virality score","segments":[{"chapter":"exact chapter name","source_beat":1,"panel_id":"exact ID from that source beat","narration":"The opening hook, grounded in the source."}]}]}

Before returning, check the complete JSON against the receipt and source package: every referenced chapter/beat/panel exists, reading order is preserved, every segment has 1–40 words, every short has 60–280 words, all events are supported, and every short delivers its promised payoff. Revise your draft before output. Return valid JSON only.
