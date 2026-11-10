How to carry out a change through Jaynes. The task is the person's request, or the plan they approved, in full,
with what was agreed in conversation.

When the request fixes or changes something Jaynes delivered before, first find the run that made it:
`jaynes delivered --project .` lists the latest deliveries with their steps and the files they changed. When one
of them made what is being fixed, record it before anything else:
`jaynes correct <run> --step <the step whose output was wrong> --kind <kind> --note "<what was wrong>"`
The kind is `error` when it was wrong by what was already known (the task, the code, or a preference already
written where the agent could read it), `preference` when it is a new preference nobody had written, and `info`
when the world changed or a new fact appeared. Tell the person in one line which run it corrects and the kind,
and that they can change the kind on the run's `page`, as printed. Then carry out the fix as below.

1. From the project's folder, run `jaynes roles`: one line of JSON per role a run on this project can start by name,
   from the packages of roles it uses, with what it is for (`about`); the person's own packages come first, and
   `plans` is true for a role that plans a request. If one plans, and the
   request is more than a small change, run it instead of picking a role yourself, as in 2 with its name and the
   request in the person's words as the task: when it delivered its plan, Jaynes starts the plan by itself, and the
   line it prints carries the `request`; tell the person the plan in one line, the runs in order, with the
   request's `page`, and go on as in 5 with `jaynes wait <request>`. Otherwise pick the role from the task, among
   the roles it listed, the first that fits by its `about`, such as:
   - `small-change` if it is small: a few lines in one or two files, such as a typo, a rename, a value, a message;
   - `fix-bug` if it describes a bug;
   - `build-feature` if it adds or changes what the code does;
   - `screen-design` if it designs a screen from how the person uses it, and they keep a design method in a
     file: it needs `--input method=<file> --input heuristics=<file>`, a preview, and `--preview-path <the
     screen>`; add the theme they judge in, a copy of the data the product reads (`--input data=<folder>:<where
     in the project>`), and their measuring tools, if they named them;
   - `redesign` if it names links or pictures of interfaces the person likes;
   - `visual-identity` if it is about the look: colors, fonts, a style;
   - a role of a package of the person's own when its `about` fits the task better.
   If none fits, or the folder is not in a git repository, do the task yourself as usual and say nothing about
   Jaynes.
   If the request needs more than one of these roles, one building on another, such as a look with
   `visual-identity` and then a screen in it, do not pick one: plan it as in 5.
2. In the background, from the project's folder, run
   `jaynes run --here --role <role> --task "<the task>" --project .`
   If the project has a dev server, or the task adds one, always add `--preview "<its command, with {port} as
   the port>"`, even when the task changes only part of a screen: without it the person merges a screen they
   never saw, and it cannot be added once the run has started.
   Jaynes works from the project's last commit: uncommitted changes are not in the run.
   Say nothing about Jaynes while it works.
3. When the run ends it prints one line of JSON. By its `status`:
   - `waiting`: the run went as far as it can without the person. Its `stops` wait for them on the page, and
     when `branch` is printed the change is already on it. When `merged` is printed, Jaynes already merged it
     into that branch by itself, since its hidden checks passed: say so in the same line, and never merge or
     offer to; the files in this folder changed with it, so read a file again before you edit it. Tell them in one line its `question` and its `page` link, as printed, and how many stops wait when
     there are more than one; then run `jaynes wait <run>` in the background: it returns once they answered, with
     a new line of JSON, and you come back to this step with it. A return still corrects the branch, and a merged
     change is merged again on top.
   - `blocked`: tell the person why, from `reasons`, in a sentence or two, with the `page` link.
   - `paused`: the agent's plan ran out; the run continues by itself at `until`.
   - `halted`: the person halted everything on the page; tell them in one line that the run goes on once they
     resume there, with the `page` link.
   - `finished`: when `merged` is printed, tell the person in one sentence that the change is merged into that
     branch, and that Undo on the run's page reverts it. Otherwise tell them that the change is on the branch
     `jaynes/<run>`, with `not_merged` in a few words when it is printed, and offer to merge it.
4. Never answer a stop yourself and never choose for the person: only the person answers, on the page. Never
   open the page's link yourself; Jaynes calls the person to it.
5. A plan: write it as JSON to a file outside the project, such as `/tmp/plan.json`:
   `{"request": "<the request, in the person's words>", "runs": [{"name": "look", "role": "visual-identity",
   "task": "<what this run does>"}, {"name": "screen", "role": "screen-design", "task": "<...>", "after":
   ["look"]}]}`, each role by its name as `jaynes roles` lists it. A run lists in `after` the runs it builds on, which come before it; runs that build on none of
   each other run at the same time. Give each run what `jaynes run` would take as fields: `preview`,
   `preview_path`, `preview_ports`, `suite` and `inputs` (an object of name to value). Then, in the background,
   from the project's folder, run `jaynes plan <the file> --project .`
   Each run starts once the runs it builds on delivered, from where their delivery landed, and its task names
   what they delivered; it never waits for their samples. When it is refused, it says why: fix the plan and run
   it again. It prints one line of JSON with the `request`, its `page`, and each run's `state` (planned,
   working, delivered, stopped, or dropped: the person took it out before it started). Tell the person the plan in one line, the runs in order, with the `page`
   link, then run `jaynes wait <request>` in the background. It returns with a new line of JSON when the
   request's `status` is `delivered` or `stopped`, or when a run `holds` for the person: tell them its
   `question` or `reasons`, with its `page`, and wait again; when it delivered, say in one sentence where each
   run landed.
