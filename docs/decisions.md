# Decisions

What was decided, what it costs, and what is still open. Newest first.

## 2026-11-07: What counts as proof that a step got it right

**Decided.** The main proof comes from outside: the real run passed and no
defect traced to that step appeared for a while afterwards. The person's answer
counts only when they actually looked, and is used for the samples; there the
stop asks a concrete question instead of offering only Accept.

**Why.** Today every Accept counts as a correct answer, and many were given
without reading, so the count said a step was good when nobody had checked it.
The only correction is sending a run back to an earlier step inside the same
run. A defect found in real use after delivery never reached the count.

**Tradeoffs.**

- A step earns trust more slowly, since it waits for the time after delivery.
- Someone has to say which step a later defect came from.

**Open.** How long to wait after delivery, and who traces a defect to its step.

## 2026-11-07: Where a project's context lives, and how a step gets it

**Decided.** A job title is not the unit of a specialist; it needs finer pieces.
The same specialist works on many projects, each for a different owner, so
context sits in three layers:

- The specialist's own roles, checks, techniques and general lessons travel to
  every project.
- A project's identity, users, decisions, glossary, real data and the history
  of its runs live in that project's repository, versioned with its code.
- The person's taste splits in two: general taste travels, and taste about one
  project lives in that project's repository.

A step gets context by address, not by similarity. The run knows its project,
each step declares the context it receives, and the record keeps what it was
shown. The agent may read more files, but only inside its copy of that project.
No similarity search (RAG) for now.

**Why.** One project's context files are about 27 KB today, about 7 thousand
tokens, and fit whole. A search by similarity across projects can return a
plausible fact from another project, and makes it hard to say afterwards why a
passage was sent. Some projects hold data from the companies the person works
for, which must never reach another project.

**Tradeoffs.**

- Taste kept in each repository cannot be seen in one place across projects.
- A step that declares its context misses what nobody declared; reading its
  own copy of the project covers part of that.

**Later.** Similarity search only when a project's history or references
outgrow what fits, such as hundreds of runs or a large reference library; even
then only after filtering by project, and with what was sent recorded.

## 2026-11-06: Jaynes is the engine of a dark software factory

**Decided.** Agents conduct the development cycle. The person writes what they
want and supervises results and exceptions: on the loop, not in it. Work
focuses on the roles; the engine works and needs improvements, not a rewrite.

**Agreed direction, not started.**

- When the external proof passes, the merge happens alone and the person sees
  the result afterwards. Irreversible effects, such as publishing or sending a
  message, keep a stop.
- External proof comes before automatic merge. External proof means checks the
  building agent cannot see or shape, and a run against the real service.

**Why.** On one project built from nothing, 15 delivered runs took about 71
hours, about 67 of them at stops, while agents worked less than 4. The first
screen came about 44 hours in, because the first slices were internal packages.
The stops were accepted without being read. The serious defects appeared only
in real use: one feature passed every agreed example and left most messages
unprocessed on its first real run.

This is one project, one person and a second reader nearby, so it does not show
that stops fail in general.

**Tradeoffs.**

- Automatic merge without external proof would have shipped that defect.
- A misunderstood task surfaces after the build, not before it.
- Hidden checks and extra agents cost tokens, and Jaynes already cost 23% to 61%
  more than direct work in three single measurements.
- A reviewing agent shares the blind spots of the model it reviews, so it does
  not replace a real run.

**Open.**

- A role for new projects whose first slice shows something working.
- A level per step: Jaynes alone, ask on doubt, ask by sample, always ask.
- A reviewing agent that leaves a short recommendation.
- Who writes the hidden scenarios, and who names the real command.
- A queue that starts runs, and a way to halt and undo.

**Not copied from existing factories.** Behavioral clones of external services
(one real run is cheaper at this scale), and removing the person from every
decision.
