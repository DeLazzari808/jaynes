How to carry out a change through Jaynes. The task is the person's request, or the plan they approved, in full,
with what was agreed in conversation.

1. Pick the role from the task:
   - `small-change` if it is small: a few lines in one or two files, such as a typo, a rename, a value, a message;
   - `fix-bug` if it describes a bug;
   - `build-feature` if it adds or changes what the code does;
   - `screen-design` if it designs a screen from how the person uses it, and they keep a design method in a
     file: it needs `--input method=<file> --input heuristics=<file>`, a preview, and `--preview-path <the
     screen>`; add the theme they judge in, a copy of the data the product reads (`--input data=<folder>:<where
     in the project>`), and their measuring tools, if they named them;
   - `redesign` if it names links or pictures of interfaces the person likes;
   - `visual-identity` if it is about the look: colors, fonts, a style.
   If none fits, or the folder is not in a git repository, do the task yourself as usual and say nothing about
   Jaynes.
2. In the background, from the project's folder, run
   `jaynes run --here --role <role> --task "<the task>" --project .`
   If the project has a dev server, add `--preview "<its command, with {port} as the port>"`.
   Jaynes works from the project's last commit: uncommitted changes are not in the run.
   Say nothing about Jaynes while it works.
3. When the run ends it prints one line of JSON. By its `status`:
   - `waiting`: run `jaynes ask <run>` and call AskUserQuestion with the JSON it prints as the input, exactly
     as printed, with nothing changed or added. The tool result then says what Jaynes recorded. When it says to
     continue, run `jaynes run <run>` in the background and come back to this step; otherwise do what it says.
     If `jaynes ask` fails, tell the person what it printed.
   - `blocked`: tell the person why, from `reasons`, in a sentence or two.
   - `paused`: the agent's plan ran out; the run continues by itself at `until`.
   - `finished`: tell the person, in one sentence, that the change is on the branch `jaynes/<run>`, and offer to
     merge it.
4. Never answer a stop yourself and never choose for the person: only the person answers, in the dialog.
   Never open the review page's link. The person can see a stop in full on the review page, opened by `jaynes`.
