"""Cross-process tests for the single-instance guard and activation IPC."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

try:
    from assistant.single_instance import SingleInstance
    from PySide6.QtCore import QCoreApplication
    _QT_APP = QCoreApplication.instance() or QCoreApplication([])
    HAVE_QT = True
except Exception:
    SingleInstance = None
    QCoreApplication = None
    _QT_APP = None
    HAVE_QT = False


_SERVER_SCRIPT = r"""
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from assistant.single_instance import SingleInstance

app = QCoreApplication([])
key, ready_path, activated_path = sys.argv[1:4]
instance = SingleInstance(key=key)
if not instance.listen():
    raise SystemExit(3)

def write_marker(path):
    target = Path(path)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text("ok", encoding="utf-8")
    temporary.replace(target)

instance.activated.connect(lambda: write_marker(activated_path))
write_marker(ready_path)
app.aboutToQuit.connect(instance.close)
raise SystemExit(app.exec())
"""


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class SingleInstanceTests(unittest.TestCase):
    def _wait_for(self, condition, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _QT_APP is not None:
                _QT_APP.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        if _QT_APP is not None:
            _QT_APP.processEvents()
        return bool(condition())

    def test_second_instance_detects_and_notifies_first(self):
        """Run the owner in another OS process so its Qt event loop stays live."""
        key = "LocalAssistant-test-" + uuid.uuid4().hex
        project_root = Path(__file__).resolve().parents[1]

        with tempfile.TemporaryDirectory(prefix="localassistant-instance-") as temp_dir:
            temp = Path(temp_dir)
            ready = temp / "ready.marker"
            activated = temp / "activated.marker"
            first_process = subprocess.Popen(
                [sys.executable, "-c", _SERVER_SCRIPT, key, str(ready), str(activated)],
                cwd=project_root,
                env=os.environ.copy(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            second = SingleInstance(key=key)
            self.addCleanup(second.close)
            try:
                self.assertTrue(
                    self._wait_for(lambda: ready.exists() or first_process.poll() is not None, 5.0),
                    "The first process did not become ready in time.",
                )
                self.assertTrue(
                    ready.exists(),
                    "The first process failed before opening its IPC server. "
                    f"Exit code: {first_process.poll()}",
                )
                self.assertFalse(second.listen())
                self.assertTrue(second.existing_instance_notified)
                self.assertTrue(
                    self._wait_for(lambda: activated.exists(), 3.0),
                    "The first process did not receive the activation request.",
                )
                self.assertEqual(activated.read_text(encoding="utf-8"), "ok")
            finally:
                if first_process.poll() is None:
                    first_process.terminate()
                try:
                    first_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    first_process.kill()
                    first_process.wait(timeout=3)
                # Drain the small diagnostic streams so Windows can release handles.
                first_process.communicate(timeout=1)

    def test_lock_is_released_on_close(self):
        key = "LocalAssistant-test-" + uuid.uuid4().hex
        first = SingleInstance(key=key)
        second = SingleInstance(key=key)
        self.addCleanup(first.close)
        self.addCleanup(second.close)

        self.assertTrue(first.listen())
        first.close()
        self.assertTrue(second.listen())


if __name__ == "__main__":
    unittest.main()
