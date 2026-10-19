---
name: find-moments
description: Use when the user asks where, when or how often something happens, is said or is shown in the video ("find the part where...", "every time...", "when does...").
---

# Find moments in the video

1. Decide whether the moment is said, seen, or both.
2. **Said:** `search` with the user's words, then again with two or three synonyms or likely phrasings. Read each hit's surroundings with `get_transcript` (about 15 s either side) to confirm it really is the moment, not just a word match.
3. **Seen:** use the overview's chapters and the transcript to narrow the range, then `inspect` that range with a question naming exactly what to look for. Use `get_frames` to confirm a single instant.
4. Report every confirmed moment, in time order, one line each: `[mm:ss–mm:ss]` and what happens there. Say how many you found.
5. If nothing matches, say so and list what you searched and which ranges you inspected. Do not offer a near miss as a match; label it "closest".

Never report a time you did not see in a tool result.
