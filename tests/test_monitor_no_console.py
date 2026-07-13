import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import check_bababot_health, restart_bababot, watch_bababot_errors


@unittest.skipUnless(os.name == "nt", "Windows console flags only apply on Windows")
class MonitorNoConsoleTests(unittest.TestCase):
    def _assert_process_scan_is_hidden(self, module):
        completed = SimpleNamespace(stdout="", stderr="", returncode=0, args=[])
        with patch.object(module.subprocess, "run", return_value=completed) as run:
            self.assertEqual(module.list_python_processes(), [])
        self.assertNotEqual(module.WINDOWS_NO_WINDOW, 0)
        self.assertEqual(run.call_args.kwargs["creationflags"], module.WINDOWS_NO_WINDOW)

    def test_watchdog_wmic_does_not_create_a_console(self):
        self._assert_process_scan_is_hidden(watch_bababot_errors)

    def test_health_check_wmic_does_not_create_a_console(self):
        self._assert_process_scan_is_hidden(check_bababot_health)

    def test_restart_wmic_and_taskkill_do_not_create_a_console(self):
        self._assert_process_scan_is_hidden(restart_bababot)

        completed = SimpleNamespace(stdout="", stderr="", returncode=0, args=[])
        with patch.object(restart_bababot.subprocess, "run", return_value=completed) as run:
            restart_bababot.terminate_process(123)
        self.assertEqual(run.call_args.kwargs["creationflags"], restart_bababot.WINDOWS_NO_WINDOW)


if __name__ == "__main__":
    unittest.main()
