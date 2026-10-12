---
name: add-questions
description: Write and add multiple-choice questions to a Quiz API deck. Use when the user wants to add, write, generate or import questions, turn notes or a topic into quiz questions, or fill out a deck.
---

# Add questions

Uses the Quiz API MCP tools. If none are available, see the `quiz-api` skill's Connecting section.

1. **Pick the deck.** `list-decks`. If the user named one, use it; if it doesn't exist, `create-deck` (with
   `parent_id` to nest it) after confirming the name.
2. **Get the source.** A topic, pasted notes or a file. If it's vague, ask one question for scope and how many
   questions they want (default 5).
3. **Avoid duplicates.** `search-questions` for the topic; skip or reword anything already covered.
4. **Draft.**
   - `single`: exactly 4 options, 1 correct. `select_two`: exactly 5 options, 2 correct.
   - Test understanding with scenarios ("A team needs X under constraint Y, which..."), not trivia.
   - Distractors are plausible (real services, real misconceptions), similar in length and style to the answer.
     No "all of the above", no giveaway wording.
   - Markdown everywhere. Every option gets an explanation of why it's right or wrong, plus an overall `explanation`.
   - Vary which position holds the correct answer.
5. **Show the draft** (stem, options, correct answer) and let the user edit or approve before saving.
6. **Save** with `create-questions` (1-50 per call). One invalid question rejects the whole batch: fix the one
   it names and resend.
7. **Report** how many were added and to which deck. Edits later go through `update-question` (pass each option's
   `id` back to keep it; `reset_progress` only if what the question tests changed).
