"""What some tests need from the machine, and why they skip without it."""

from __future__ import annotations

import pytest

from jaynes import sandbox
from jaynes.roles.references import chrome

# inside a box, as when Jaynes's own suite runs in a run's box, Chrome opens only with its own sandbox off and the
# shared temporary folder writable, which a box must not allow
chrome_runs = pytest.mark.skipif(chrome() is None or sandbox.inside(),
                                 reason="needs Google Chrome, which does not open inside a box")
