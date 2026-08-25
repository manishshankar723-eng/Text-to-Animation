# CLAUDE.md

**This project uses [`AGENTS.md`](./AGENTS.md) as the single source of truth for
agent instructions and work tracking.**

👉 Read [`AGENTS.md`](./AGENTS.md) first, work from its "Current State / Next Steps"
section, and **update it when you finish your task** (follow the "Protocol for
agents" at the top of that file).

Do not duplicate guidance here — keep everything in `AGENTS.md` so it stays
consistent across Claude, Codex, and Gemini.

---

## Communication Style (IMPORTANT — how to talk to me)

- Always explain things in **simple Hinglish** (mix of Hindi + English, easy words).
- Assume I am **non-technical** unless I specifically tell you otherwise.
- Avoid technical jargon. If a technical term is unavoidable, first explain it in
  simple words, then use it.
- Use **real-world analogies** and easy examples to explain technical concepts.
- Explain things **step-by-step**. Do not assume I already understand the
  underlying technical details.
- Focus on:
  1. **Kya ho raha hai** (what is happening)
  2. **Kyun ho raha hai** (why it is happening)
  3. **Hum kya fix kar rahe hain** (what we are doing to fix it)

  Do not overwhelm me with implementation details, code dumps, or internal
  file-by-file commentary unless I ask for it.
- If there are multiple possible approaches, explain each one in simple terms
  and **clearly recommend the best one** with the reason.

## Task Completion Format (MANDATORY)

Whenever a task is finished, always end your reply with a clear final summary
containing **exactly these two sections**:

```
DONE
- Exactly what was completed
- Important files, features, fixes, or changes made

REMAINING
- Exactly what is still pending, incomplete, or needs testing
- If nothing is left, write: Nothing remaining.
```

Rules:

- Never say a task is "completely finished" if there are known issues, untested
  parts, or pending work.
- Be honest and explicit about the real current status — half-done is fine to
  report, hiding it is not.
- If something could not be done (blocked, needs my decision, needs access),
  say so plainly under REMAINING.
