---
name: jaynes
description: Run a task with Jaynes, which conducts Claude Code through a role's method one step at a time and stops for the person's answer. Use when the person types /jaynes or asks to run a task with Jaynes.
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
   `jaynes run --here --role <role> --task "<the task>" --project .`
   If the project has a dev server, add `--preview "<its command, with {port} as the port>"`.
   Say nothing about Jaynes while it works.
4. When the run ends it prints one line of JSON. By its `status`:
   - `waiting`: run `jaynes ask <run>` and call AskUserQuestion with the JSON it prints as the input, exactly
     as printed, with nothing changed or added. The tool result then says what Jaynes recorded. When it says to
     continue, run `jaynes run <run>` in the background and come back to this step; otherwise do what it says.
     If `jaynes ask` fails, tell the person what it printed.
   - `blocked`: tell the person why, from `reasons`, in a sentence or two.
   - `paused`: the agent's plan ran out; the run continues by itself at `until`.
   - `finished`: tell the person it is done, in one sentence.
5. Never answer a stop yourself and never choose for the person: only the person answers, in the dialog.
   Never open the review page's link. The person can see a stop in full on the review page, opened by `jaynes`.
