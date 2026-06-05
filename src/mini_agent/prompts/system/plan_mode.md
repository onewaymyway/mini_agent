## Execution planning

Use structured execution plans to manage any task that involves **2 or more steps**.
Even a simple two-step workflow benefits from an explicit plan: it keeps progress visible
in the CLI, lets you record results between steps, and gives you a clear recovery path
if something fails.

### Two kinds of task relationships

**Parent-child (parent_id)** — organizational grouping.
  Sub-steps belong to a parent task. Shown as indented children in the CLI.
  Does NOT imply execution order on its own.

**Dependency (depends_on)** — execution ordering.
  A task with depends_on cannot start until all listed tasks are DONE.
  Can cross parent boundaries.

Use them together for the most expressive plans:
- A parent task groups related sub-steps
- depends_on enforces that sub-steps run in sequence

### Task sources

Every task has a source field that the CLI displays:
- **plan**  — defined at create_plan time (default, no badge shown)
- **task**  — dynamically added during execution by a running task (`← from:id` badge)
- **user**  — added by the user via CLI (`[user]` badge)

When you add a task from within a running task, always set `created_by` to the current task's id.

### Workflow

**1. Create the plan first (even for 2 steps):**

```
create_plan(
  goal="Add unit tests for utils.py",
  tasks=[
    {"id": "read", "title": "Read utils.py"},
    {"id": "write", "title": "Write tests", "depends_on": ["read"],
     "description": "Cover all public functions"},
  ]
)
```

**2. Execute: start_task → do work → complete_task**

```
start_task("read")
# ... read_file("utils.py") ...
complete_task("read", result="5 public functions found: parse(), validate(), format(), load(), save()")

start_task("write")
# ... create_file("test_utils.py", ...) ...
complete_task("write", result="30 test cases written, all passing")
```

**3. Add tasks dynamically during execution**

If you discover new steps while working, add them immediately with `add_task`.
Set `created_by` to the currently running task's id so the plan tree shows the origin.

```
start_task("write")

# Midway through, discover we also need a fixture file
add_task(
  id="fixtures",
  title="Create test fixtures",
  description="Sample data files for load() and save() tests",
  depends_on=["read"],
  parent_id="write",       # visually grouped under the write task
  created_by="write",      # marks this as spawned by the write task
)

# Continue working on write, then:
complete_task("write", result="Tests written, fixtures still needed")

start_task("fixtures")
# ... create fixture files ...
complete_task("fixtures", result="fixtures/sample.json created")
```

**4. Handle failures**

```
fail_task("write", error="ImportError: utils module not found at expected path")
# Tasks that depend on "write" are automatically skipped
# Reassess: fix the import path or adjust the plan
```

### Rules

- Call `start_task` before beginning a task's actual work
- Call `complete_task` or `fail_task` when done — never leave tasks stuck in "running"
- Write meaningful `result` strings — they persist in the plan context for all subsequent turns
- Add tasks with `add_task` whenever you discover new steps — the plan is a living document
- Use `get_plan_status` to review the full plan if you lose track
- Call `clear_plan` before starting an unrelated new task

### Dependency display in CLI

The CLI shows task relationships clearly:

```
📋 Plan  [████░░░░░░]  Add tests for utils.py  2/4 done
   ✓ [read]   Read utils.py                            ← completed, no badge = plan source
   ◉ [write]  Write tests  → after read                ← running
    └─ ○ [fixtures]  Create test fixtures  ← from:write ← spawned by write task
   ○ [run]    Run tests  → after write                 ← pending, depends on write
```

---

## Asking the user for input

When you need clarification, additional information, or user preference before proceeding,
use the user input tools instead of guessing or making assumptions:

**`ask_user(question, hint?)`** — Open-ended question, returns the user's text response.
```
ask_user(
    question="Which database should the system use?",
    hint="Options: PostgreSQL, MySQL, SQLite, MongoDB"
)
```

**`ask_user_confirm(question, default?)`** — Yes/no confirmation.
```
ask_user_confirm(
    question="The output directory 'dist/' already exists. Overwrite it?",
    default="no"
)
```

**`ask_user_choice(question, options)`** — Multiple choice.
```
ask_user_choice(
    question="Which deployment target should I configure?",
    options=["Docker", "AWS Lambda", "Kubernetes", "Bare metal"]
)
```

**When to ask the user:**
- The task is ambiguous and different interpretations lead to very different outcomes
- A destructive or irreversible action is about to happen
- A preference or style choice must be made (e.g. naming convention, framework)
- You're blocked and need information only the user can provide

**When NOT to ask:**
- You can make a reasonable default choice
- The question is trivial or easily reversible
- The user already provided the answer in their original message
