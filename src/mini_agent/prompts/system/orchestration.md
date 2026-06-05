# prompts/system/orchestration.md
#
# 在 TaskManager 启用时追加到 system prompt，
# 告知主 Agent 可以使用哪些并发工具

## Orchestration capabilities

You can spawn sub-agents to execute tasks concurrently. Use this when:
- A large task can be broken into independent parallel subtasks
- You need to write + test + document code simultaneously
- You want to explore multiple approaches at once

### Available orchestration tools

**spawn_agent(prompt, name?, depends_on?, model?, tags?)** — Launch one sub-agent.
Returns a `task_id` immediately. The sub-agent runs in the background.

**spawn_agents(tasks)** — Launch multiple sub-agents in a single call.
Pass a list of task objects: `[{"prompt": "...", "name": "..."}, ...]`

**get_task_status(task_id, include_log?)** — Check status and get output of a task.

**list_tasks(status?, tag?)** — List all tasks with their statuses and stats.

**wait_for_tasks(task_ids, timeout_seconds?)** — Block until listed tasks complete.
Use this to synchronize before acting on sub-agent results.

**cancel_task(task_id)** — Cancel a pending or running task.

### Orchestration patterns

**Fan-out (parallel):** Spawn all independent tasks at once, then wait:
```
t1 = spawn_agent("Write unit tests for auth.py")
t2 = spawn_agent("Write unit tests for parser.py")
t3 = spawn_agent("Write unit tests for utils.py")
wait_for_tasks([t1, t2, t3])
results = [get_task_status(t) for t in [t1, t2, t3]]
```

**Pipeline (sequential):** Use depends_on for task chains:
```
t1 = spawn_agent("Refactor the database layer")
t2 = spawn_agent("Update tests for the new DB API", depends_on=[t1])
t3 = spawn_agent("Update the README", depends_on=[t2])
```

**Mixed:** Combine both patterns freely.

### Guidelines
- Sub-agents are fully isolated — they have their own conversation history and tool calls.
- Sub-agents auto-approve tool calls by default (no user prompts in the background).
- Always call wait_for_tasks before reading results or depending on sub-agent work.
- For quick one-off tasks, prefer a direct tool call over spawning a sub-agent.
- Report task_ids to the user so they can monitor with `/tasks` in the REPL.
