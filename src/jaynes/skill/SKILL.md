---
name: jaynes
description: Carry out an agreed code change through Jaynes, which runs Claude Code through a method one step at a time, checks each step by code and stops for the person's answer where it matters. Use whenever the person approves a plan or a change to a project's code in this conversation ("faça", "pode fazer", "go ahead", "do it", "ship it"), and when they ask to run a task with Jaynes. Not when they say to do it without Jaynes ("sem a Jaynes", "without Jaynes"): then make the change yourself.
argument-hint: <task>
---

Carry out this change through Jaynes: $ARGUMENTS

When the skill starts from an approved plan, the task is that plan, in full, with what was agreed in conversation.

1. Run `jaynes update`. If `jaynes` is not found or has no `guide` command, run
   `uv tool install --reinstall git+https://github.com/alk0x1/jaynes` (install uv first if it is missing).
2. Run `jaynes guide` and follow what it prints.
