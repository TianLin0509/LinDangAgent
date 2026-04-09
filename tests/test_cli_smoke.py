"""Smoke tests for CLI command registration.

Verifies that every registered command in COMMANDS can at least be
imported without crashing.  Does NOT call any command handler.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestCliImport(unittest.TestCase):
    """Verify cli.py imports cleanly and COMMANDS dict is well-formed."""

    def test_cli_module_imports(self):
        """cli.py should import without errors."""
        import cli  # noqa: F401

    def test_commands_dict_exists(self):
        import cli
        self.assertTrue(hasattr(cli, "COMMANDS"))
        self.assertIsInstance(cli.COMMANDS, dict)

    def test_commands_are_callable(self):
        """Every value in COMMANDS must be callable."""
        import cli
        for name, handler in cli.COMMANDS.items():
            with self.subTest(command=name):
                self.assertTrue(callable(handler), f"COMMANDS[{name!r}] is not callable")

    def test_no_empty_command_names(self):
        import cli
        for name in cli.COMMANDS:
            self.assertTrue(name.strip(), "Empty command name in COMMANDS")

    def test_public_commands_no_underscore(self):
        """Public commands should not start with underscore (internal ones do)."""
        import cli
        public = [k for k in cli.COMMANDS if not k.startswith("_")]
        internal = [k for k in cli.COMMANDS if k.startswith("_")]
        self.assertGreater(len(public), 30, "Expected 30+ public commands")
        self.assertGreater(len(internal), 0, "Expected some internal commands")

    def test_core_commands_registered(self):
        """Essential commands must be present."""
        import cli
        essential = [
            "analyze", "war-room", "kline",
            "top10-query", "top10-generate",
            "sentiment-query", "health",
            "list-models", "reports",
        ]
        for cmd in essential:
            with self.subTest(command=cmd):
                self.assertIn(cmd, cli.COMMANDS, f"Essential command {cmd!r} missing")


class TestCoreModulesImport(unittest.TestCase):
    """Verify core modules import without errors."""

    def test_import_analysis_service(self):
        from services import analysis_service  # noqa: F401

    def test_import_war_room(self):
        from services import war_room  # noqa: F401

    def test_import_rank_service(self):
        from services import rank_service  # noqa: F401

    def test_import_ai_client(self):
        from ai import client  # noqa: F401

    def test_import_config(self):
        import config  # noqa: F401

    def test_import_tushare_client(self):
        from data import tushare_client  # noqa: F401

    def test_import_indicators(self):
        from data import indicators  # noqa: F401

    def test_import_report_data(self):
        from data import report_data  # noqa: F401

    def test_import_prompts(self):
        from ai import prompts  # noqa: F401
        from ai import prompts_report  # noqa: F401
        from ai import prompts_war_room  # noqa: F401


if __name__ == "__main__":
    unittest.main()
