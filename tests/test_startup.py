from __future__ import annotations

import subprocess
import sys


def test_importing_api_does_not_load_paid_sdks() -> None:
    script = (
        "import sys, living_kb.api; "
        "print([m for m in sys.modules if m.split('.')[0] in ('openai', 'anthropic')])"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "[]"
