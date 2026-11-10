# Roles that come with Jaynes

Jaynes comes with two packages of roles, used by every project until you say otherwise: `software`, for code, and `design`, for the look and the screens. Your agent picks the role from your request; on the page, New run lists them. To see what a project can start, run `jaynes roles` in it.

## software

### small-change

The light path, for a few lines in one or two files, such as a typo, a rename, a value or a message. The agent makes the change and Jaynes runs your test suite, with no stop, unless the change has more than 30 changed lines or touches a file that runs later. Then you see it.

### fix-bug

1. Jaynes finds how the project runs its tests: pytest, a `test` script in `package.json`, `go test`, `cargo test` or a Makefile `test` target. The agent never chooses what counts as the suite. When Jaynes finds none, the run stops and asks you to start it again with `--suite "<command>"` (Test suite, on New run).
2. The agent writes a test that fails because of the bug, changing test files only. Jaynes runs it in the box and requires it to fail with the message the agent quotes. You see the test.
3. The agent fixes the code, never the tests, until Jaynes sees those tests and the whole suite pass.

If the agent finds no bug a test can show, it says why and you decide: accepting ends the run with nothing changed. Checks written from your request alone, which the agent never sees, prove the fix, and Jaynes merges it by itself when they pass (see [Merging by itself](runs.md#merging-by-itself)).

### build-feature

The same way as fix-bug, from the plan you approved:

1. Tests that fail because the feature does not exist yet.
2. A stop where you see one to five examples of what they check, each with a sketch of its screen, drawn by Jaynes's Chrome from the agent's page.
3. The code, until the tests and the suite pass.
4. A stop before the merge, with each page photographed on the real screen when the run has a preview. That photo shows what the product reads on your computer, your data included, and stays on it.

The code keeps the project's look: Jaynes reads the colors and fonts the project's files already set, and refuses a sketch or a screen that brings others. Changing them is visual-identity's work.

## design

The design roles are examples: they show how far a role can go, and your own design method will fit you better. See [Packages](packages.md) to use yours in their place.

### visual-identity

The agent reads the project's current colors and fonts, proposes a direction, and applies it to the style files. Jaynes checks the direction's contrast.

### redesign

Goes further than visual-identity: it also changes motion and the markup of the screens (HTML, JSX, TSX, Vue, Svelte), keeping what each screen does. It starts from references: put one or more links or image paths of interfaces you like in the task. Jaynes captures each link with headless Chrome and gives the pictures to the agent, and the direction must say what it takes from each one.

Agents run without the web unless a step allows it. The direction step does, so the agent can read the references' sites and look up fonts, and the stop lists every address it read. Shell commands stay offline.

### screen-design

Carries out your own design method on one screen. You give the method and your finishing heuristics as files:

```sh
jaynes run --role screen-design --task "The People screen" --project . \
  --input method=method.md --input heuristics=heuristics.md \
  --preview "npm run dev -- --port {port}" --preview-path /people
```

- Jaynes opens the screen itself, in headless Chrome, in the theme you judge in (`--input theme=dark`), with a copy of the data the product reads, frozen at the start (`--input data=~/app/data:data`), so it never touches live data.
- You choose and order the moments where you get stuck, from cases quoted from the screen.
- The agent maps the objects you think in, and writes each moment's path in a closed vocabulary: open, click a text, open a row, press a button, scroll, type, back. Jaynes walks each path, refusing every request but the product's own GETs, minus those you list as having effects (`--input blocked_gets=api/logo`).
- You approve one requirement per break, and the first slice, with where its moment must end. The slice's test is a path that must fail on the screen as it is and pass once it is built.
- Your own measuring tools check it (`--input measure=...`, `--input compare=...`, each printing `{"failures": [...]}`), against the version you last approved (`--input approved=<commit>`).
- A reviewer looks at captures in both themes, on desktop and phone, and at each transition, and sends what fails back to the implementer.
- The final stop shows the tokens each step spent against the budget (`--input budget=150000`). After it, every note you gave becomes a heuristic for your file.

Agents read a summary of each API response, and a number they write must name a query that Jaynes computes.
