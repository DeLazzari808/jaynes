---
name: jaynes
description: Run a task with Jaynes, which conducts Claude Code through a role's method one step at a time and stops for the person's answer in its review page. Use when the person types /jaynes or asks to run a task with Jaynes.
argument-hint: <task>
---

Run this task with Jaynes: $ARGUMENTS

If no task is given, ask for one first.

1. Run `jaynes update`. If `jaynes` is not found or has no `update` command, run
   `uv tool install --reinstall git+https://github.com/alk0x1/jaynes` (install uv first if it is missing).
2. Pick the role from the task:
   - `fix-bug` if it describes a bug;
   - `redesign` if it names links or pictures of interfaces the person likes;
   - `visual-identity` if it is about the look: colors, fonts, a style.
   If none fits, say Jaynes has no role for this task yet, name these three, and stop.
3. In the background, from the project's folder, run
   `jaynes run --role <role> --task "<the task>" --project .`
   If the project has a dev server, add `--preview "<its command, with {port} as the port>"`.
   The run opens the review page in the browser by itself, and the page calls the person at each stop.
4. When the run ends it prints one line of JSON. Tell the person what it means, in one sentence:
   `waiting`, the run waits for their answer in the review page; `blocked`, why, from `reasons`;
   `paused`, the agent's plan ran out and the run continues by itself at `until`; `finished`, it is done.
5. Never answer a stop yourself, and never open the review page's link: only the person answers.
