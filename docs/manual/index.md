# Get started

Jaynes runs your agents through a method one step at a time, checks each step by code, and calls you only where your judgment is still needed. You install it once. After that you work in Claude Code as usual, and answer in a page on your computer.

## Install

Type this in Claude Code, answer `y` to add the marketplace, and press Enter to install it for your user:

```text
/plugin install jaynes --marketplace alk0x1/jaynes
```

The first time your agent uses it, the agent installs the `jaynes` command with [uv](https://docs.astral.sh/uv/). It needs Python 3.12 or later. Jaynes runs on macOS; on Linux, previews and test runs need `JAYNES_UNBOXED=1` (see [Runs and stops](runs.md#the-box)).

Without the plugin, from a terminal:

```sh
uv tool install git+https://github.com/alk0x1/jaynes
jaynes skill    # adds /jaynes to Claude Code
jaynes          # opens the page
```

## Work as usual

Talk the change through with your agent and approve the plan: "go ahead", "do it". The agent hands it to Jaynes by itself:

- It picks a **role**, the method for that kind of work, such as fix-bug or build-feature, and starts a **run**.
- A small change goes through with your test suite and no question.
- When the run needs you, the agent tells you the question in one line, with a link to it.

Say "with Jaynes" to send any task through it, or "without Jaynes" for one the agent should make itself. To turn Jaynes off in one project, run this there:

```sh
claude plugin disable jaynes@jaynes --scope local
```

## Answer where the run needs you

A **stop** is a point in the method where your judgment matters, such as a screen's final look. Most stops do not hold the run: it goes on with the agent's choice and delivers, and your answer, given later, corrects on top. What cannot be undone waits for you.

You answer in Claude Code's question dialog, when the agent shows you a stop, or on the page, which shows the stop in full. [Runs and stops](runs.md) has the answers you can give and what each one does.

## The page

The page lives at [http://127.0.0.1:8740](http://127.0.0.1:8740) on your computer. Bookmark it, or type `jaynes` in a terminal: it starts the page if needed and opens it. Any run starts it too.

| Part | What it shows |
|---|---|
| Home | What holds a run until you answer, the samples the runs went past, what was delivered since your last visit, and what runs now |
| New run | A form to start a run yourself: a role, a task, a project |
| Roles | The packages of roles you have, which projects use which, and how each role's stops are doing |
| History | How many of your agents' tool calls a person approved, read from Claude Code's transcripts |
| Log | Every event, as Jaynes recorded it |
| Setup | The guard, the optional action gate, `/jaynes`, and the version running |

When a newer Jaynes is out, the page offers to update and restarts itself.

## The guard

The plugin brings a guard to every Claude Code session where it is on, not only to runs. Before an agent does something in git or GitHub that cannot be undone, Claude Code asks you and says why: `git push`, `git reset --hard`, `git clean`, `git branch -D`, `git tag -d`, creating, merging or closing a pull request, and creating, deleting or archiving a repository. It asks instead of blocking, so you approve with one key.

Repositories whose owner is one of your free owners pass freely: at first your own GitHub user. Change the owners, or turn the guard off, on Setup.

## Next

- [Runs and stops](runs.md): what a run does with your project, and what each answer does.
- [Roles that come with Jaynes](roles.md): what each one does, step by step.
- [Packages](packages.md): add roles of your own or of your team, for one project or for all.
- [Write a role](write-a-role.md): your own method, in Python or TOML.
- [Commands](commands.html): every `jaynes` command.
