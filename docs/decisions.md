# Decisions

What was decided, what it costs, and what is still open. Newest first.

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
