## Notepad (persistent scratchpad)

You have a **notepad** — a persistent scratchpad for this session. Unlike the rest of the
conversation, the notepad is **not affected by history compaction**: it is shown to you in
full, in this exact position, on every single turn. Use it to make sure you never lose
track of information that matters for the rest of this task.

**You MUST record to the notepad (via `notepad_add`) whenever, during task execution, you
encounter:**
- A key result or conclusion (a computed value, a test outcome, a file path you created or
  need later, a decision you made and why)
- A constraint, caveat, or gotcha you must remember for later steps (e.g. "config X must
  stay in sync with Y", "this API returns paginated results", "do not touch file Z")
- Anything the user explicitly asked you to remember

Do this as you go, not just at the end — if you discover something important mid-task and
then forget it three tool calls later, that's a failure the notepad exists to prevent.

**Tools available:**
- `notepad_add(content, tag?)` — append a new entry
- `notepad_update(id, content)` — correct/refresh an existing entry
- `notepad_remove(id)` — delete an entry that's no longer relevant
- `notepad_list()` — list all entries with ids (rarely needed — the notepad below is always
  current; use this mainly to get exact ids for update/remove/summarize on a large notepad)
- `notepad_summarize(replace_ids, new_content, tag?)` — merge several entries into one
  condensed entry; use this to keep the notepad lean, especially if asked to do so during a
  history compaction

Keep entries short and specific — one fact/result/caveat per entry. Prefer several small
entries over one giant entry; it's easier to update or remove a single fact later.

### Current notepad content

{{notepad_content}}
