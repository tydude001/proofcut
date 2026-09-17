"""Suite-wide fixtures.

The SDK's stdio client hands a spawned server only
`get_default_environment()`'s allow-list, and `QT_QPA_PLATFORM` is not on it,
so a headless run (no desktop session) could not render through melt even
with the variable exported to pytest. Pass it through when the caller set
it, and nothing else: CLAUDE.md § the melt tests, docs/plans/SUITE-SPEED.md.
"""

from __future__ import annotations

import os

import mcp.client.stdio as _stdio
import pytest

_PASSED_THROUGH = ("QT_QPA_PLATFORM",)


@pytest.fixture(autouse=True, scope="session")
def _stdio_server_sees_qt_platform():
    original = _stdio.get_default_environment

    def widened() -> dict[str, str]:
        env = original()
        for key in _PASSED_THROUGH:
            if key in os.environ:
                env[key] = os.environ[key]
        return env

    _stdio.get_default_environment = widened
    yield
    _stdio.get_default_environment = original
