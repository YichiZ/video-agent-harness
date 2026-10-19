---
name: summarize-video
description: Use when the user asks for a summary, recap, outline, overview or "what is this video about", for the whole video or a long part of it.
---

# Summarize the video

1. Call `get_overview` if you have not already. Its chapters are the outline.
2. For each chapter, read what is said with `get_transcript` over the chapter's range. Skip this for a chapter whose overview summary already says enough and the video is long; read at least the first and last chapter.
3. If a chapter is mostly visual (little or no speech), call `inspect` once on it with "What happens here?".
4. Write the summary:
   - One or two sentences on what the video is and who is in it.
   - One bullet per chapter: `[mm:ss–mm:ss]` then what happens or is argued, in a sentence or two.
   - End with the takeaway or conclusion, cited.

Every bullet carries a citation from a tool result. If the user asked about only part of the video, summarize only that range. Keep it shorter than the video deserves rather than longer.
