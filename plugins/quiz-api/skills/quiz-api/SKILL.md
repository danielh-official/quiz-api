---
name: quiz-api
description: Study and author multiple-choice spaced-repetition quizzes through the Quiz API MCP server (tools such as list-decks, start-session, next-question, submit-answer, create-questions). Use when the user wants to study or be quizzed, review due questions, write or improve quiz questions, organise decks, or check their weak spots and progress.
---

# Quiz API

Quiz API is the user's personal spaced-repetition app for multiple-choice questions (think certification-exam
practice). You drive it through its MCP server. Scheduling uses FSRS, so honest answers and honest confidence
matter more than speed.

## Connecting

The server lives at `<APP_URL>/mcp` (streamable HTTP, OAuth with GitHub sign-in; only allowlisted GitHub users get in).

- Claude Code with the quiz-api plugin: the server is already configured. Run `/mcp`, pick quiz-api and sign in.
- Claude Code without the plugin: `claude mcp add --transport http quiz-api <APP_URL>/mcp`, then `/mcp` to sign in.
- Claude.ai / Claude Desktop: Customize → Connectors → "+" → Add custom connector → `<APP_URL>/mcp`
  (Team/Enterprise: an owner adds it first under Organization settings → Connectors). Needs a public URL.
- ChatGPT (Plus, Pro, Business, Enterprise, Edu; web): Settings → Security and login → Developer mode, then
  ChatGPT Plugins → "+" → create a developer-mode app with `<APP_URL>/mcp` and OAuth.

If no quiz-api tools are available, tell the user how to connect instead of pretending to quiz them.

## Data model

- **Decks** nest. Studying, searching and stats on a deck include all its subdecks.
- **Questions** are `single` (exactly 4 options, 1 correct) or `select_two` (exactly 5 options, 2 correct).
  Stems, options and explanations are Markdown. Every option has a stable `id`; answers use option ids.
- **Confidence** comes with every answer: `confident`, `educated_guess` or `complete_guess`.
  FSRS rating: correct + confident = Good, correct + educated_guess = Hard, anything else = Again.
- A **misconception** is a wrong answer given with confidence — the most valuable thing to catch.
- **Notes**: one private note per question for the user's own reasoning. Questions can also be **suspended**
  (skipped when studying) with `update-card`.
- **Session summary**: a free-form running summary per study session, rewritten after every answer and returned
  (latest three, any deck) by `start-session`.
- **Daily limits**: each deck's `new_per_day` caps new questions introduced per study day, counting its subdecks.
  When studying a deck, every deck between a question and the studied deck must have allowance left.
  The study day rolls over at 4am in the user's timezone.

## First use

Call `update-settings` with no arguments. If the timezone is still `UTC`, ask the user where they are and set it
(IANA name, e.g. `America/New_York`) — otherwise their daily limits roll over at the wrong time.

## Studying

1. Pick a deck (`list-decks` shows due/new counts), then `start-session` with its `deck_id`. Read its
   `recent_summaries` before the first question and use them: revisit weak spots, and don't repeat advice that
   already landed.
2. `next-question` → show the question in the format below. Keep the wording of the stem and the options
   **exactly as returned**, and the options in that order. Never hint, rephrase, or reveal which is right.
   - A heading line: `Question <answered + 1> of <size>` (from `session`).
   - The stem as a bulleted list, **one sentence per bullet**. Keep code blocks, tables and lists in the stem intact.
   - A blank line, then each option on its own line as `A. <text>`, `B. <text>`…, with a blank line between options.
   - For `select_two`, end with "Pick two."

   ```
   Question 1 of 6

   - A company runs Amazon RDS for MySQL as a Single-AZ DB instance.
   - A new reporting tool runs heavy read-only queries that slow down the application's writes.
   - Which change offloads the reporting queries with the least impact on the primary?

   A. Run the reports against the most recent automated backup snapshot

   B. Enable Multi-AZ and point the reporting tool at the standby instance

   C. Create a read replica and point the reporting tool at the replica's endpoint

   D. Move the DB instance to a larger instance class to handle both workloads
   ```
3. Ask for their pick(s) **and** their confidence before revealing anything. If they don't say, ask:
   "Confident, educated guess, or complete guess?"
4. `submit-answer` with the option **ids** (map their letters back) and the confidence.
5. Show whether they were right, the correct option(s), and the explanations. If it was a misconception, say so
   plainly and dig into why their reasoning felt right. Offer to save their takeaway with `update-card` (`note`).
6. `update-session` with the **whole** summary so far, rewritten to include this answer — every time, so nothing
   is lost if the user leaves mid-session. Capture what's worth knowing next session:
   - the user's reasoning, in their words where it matters (especially behind misconceptions);
   - your read: patterns, weak and strong topics, calibration (confident but wrong, or unsure but right);
   - anything else to remember, such as preferences or topics to revisit.

   Keep it tight (a few short bullets per topic, under 10,000 characters) and don't paste question text.
7. Repeat from step 2 until `next-question` returns `finished: true`, then present the summary
   (score, accuracy per confidence level, misconceptions).

Don't grade answers yourself or skip `submit-answer` — the schedule depends on every answer being recorded.
Don't skip `update-session` either, even for a quick session.

## Writing questions

1. `search-questions` first so you don't duplicate what exists.
2. Test understanding and judgement, not trivia: scenarios ("A team needs X under constraint Y — which…").
3. Distractors must be plausible — real services, real misconceptions — and similar in length and style to the
   correct answer. Avoid "all of the above" and giveaway wording.
4. Give **every option** an explanation of why it is right or wrong, plus an overall `explanation`.
5. Create in batches with `create-questions` (1–50 per call; the whole batch is rejected if any question is
   invalid — fix the named question and resend).
6. When editing with `update-question`, pass back each option's `id` to keep it. Set `reset_progress` only when
   the edit changes what the question tests or which answer is correct.

## Reviewing progress

`get-performance` (optionally per deck) returns due/new counts, calibration (is "confident" actually right?),
recent misconceptions, leeches (questions forgotten repeatedly) and the user's notes. Use it to suggest what to
study next, to rewrite unclear leeches, or to write new questions targeting weak spots.

For "am I ready for the exam?", pass `exam_date` and read `readiness`: `seen` of `questions` is coverage,
`predicted_recall` is the FSRS chance of remembering a seen question on that day, and `expected_score` counts
unseen questions as wrong. `by_deck` splits it per subdeck. Combine it with calibration and misconceptions (a high
recall with many confident misses means the scheduler hasn't caught up yet), name the weakest subdecks, and say
how many days of study are left and how many unseen questions that means per day.

## Limits

There are no delete tools — deleting decks or questions is done by the user through the REST API.
