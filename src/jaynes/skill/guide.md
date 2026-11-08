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
   If the project has a dev server, or the task adds one, always add `--preview "<its command, with {port} as
   the port>"`, even when the task changes only part of a screen: without it the person merges a screen they
   never saw, and it cannot be added once the run has started.
   Jaynes works from the project's last commit: uncommitted changes are not in the run.
   Say nothing about Jaynes while it works.
3. When the run ends it prints one line of JSON. By its `status`:
   - `waiting`: the run went as far as it can without the person. Its `stops` wait for them on the page, and
     when `branch` is printed the change is already on it. Tell them in one line its `question` and its `page`
     link, as printed, and how many stops wait when there are more than one; then run `jaynes wait <run>` in the
     background: it returns once they answered, with a new line of JSON, and you come back to this step with it.
     Offer the merge only at `finished`: a return still corrects the branch until then.
   - `blocked`: tell the person why, from `reasons`, in a sentence or two, with the `page` link.
   - `paused`: the agent's plan ran out; the run continues by itself at `until`.
   - `finished`: tell the person, in one sentence, that the change is on the branch `jaynes/<run>`, and offer to
     merge it.
4. Never answer a stop yourself and never choose for the person: only the person answers, on the page. Never
   open the page's link yourself; Jaynes calls the person to it.
