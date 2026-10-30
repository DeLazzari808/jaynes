#!/bin/sh
# Claude Code runs this after its question dialog, with the dialog's input and the person's answer on stdin:
# jaynes answered records the answer to a Jaynes stop. Without jaynes installed there is nothing to record.
for j in "$(command -v jaynes)" "$HOME/.local/bin/jaynes"; do
  if [ -n "$j" ] && [ -x "$j" ]; then exec "$j" answered; fi
done
cat >/dev/null
