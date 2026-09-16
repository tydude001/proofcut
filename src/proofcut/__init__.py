"""proofcut — a source-available, local-first AI video editor.

The public surface is the MCP server (``proofcut mcp``) and the equivalent CLI
(``proofcut <subcommand>``). Everything operates on a *project directory*; see
:mod:`proofcut.project` for its layout.
"""

#: Duplicated in ``pyproject.toml`` deliberately. The metadata lookup that
#: would remove the copy reads the *installed* dist-info, which goes stale
#: against an editable checkout without saying so; tests/test_version.py
#: has the reasoning and holds the two numbers together.
__version__ = "0.27.0"

__all__ = ["__version__"]
