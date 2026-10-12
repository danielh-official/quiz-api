---
name: start-session
description: Start a Quiz API study session on a deck and run the question loop. Use when the user wants to study, be quizzed, review due questions, or practice a deck.
---

# Start a study session

Uses the Quiz API MCP tools. If none are available, see the `quiz-api` skill's Connecting section.

1. **Timezone.** If this is the first use, `update-settings` with no arguments; if the timezone is `UTC`, ask where
   they are and set it (IANA name), so daily limits roll over at 4am local.
2. **Deck.** `list-decks` (shows due/new counts). Use the deck the user named, otherwise suggest the one with the
   most due questions and ask.
3. **Start.** `start-session` with `deck_id`. Read `recent_summaries` and use them: revisit weak spots, don't repeat
   advice that already landed. If there's nothing due or new, say so and stop.
4. **Loop.** Follow the `quiz-api` skill's Studying steps 2-7 exactly: show the question verbatim in its format
   (deck path, `Question N of M`, bulleted stem, lettered options), get pick **and** confidence before revealing,
   `submit-answer` with option ids, explain, then `update-session` with the whole running summary after every answer.
5. **Finish** when `next-question` returns `finished: true`: show score, accuracy per confidence level and
   misconceptions.

Never grade answers yourself or skip `submit-answer` / `update-session`.
