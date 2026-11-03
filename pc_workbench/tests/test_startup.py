import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from tools.check_startup import inspect_runtime

ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def test_missing_opencv_is_reported_before_server_start(self):
        def importer(name):
            if name == 'cv2':
                raise ModuleNotFoundError("No module named 'cv2'")
            return SimpleNamespace(__file__='/installed/' + name)
        result = inspect_runtime(importer)
        self.assertFalse(result['ok'])
        self.assertEqual(set(result['errors']), {'cv2'})

    def test_dll_load_failure_is_not_mistaken_for_installed_dependency(self):
        def importer(name):
            if name == 'numpy':
                raise ImportError('DLL load failed')
            return SimpleNamespace(__file__='/installed/' + name)
        result = inspect_runtime(importer)
        self.assertFalse(result['ok'])
        self.assertIn('DLL load failed', result['errors']['numpy'])

    def test_no_ai_imports_are_required_to_open_the_desktop(self):
        names = []
        def importer(name):
            names.append(name)
            return SimpleNamespace(__file__='/installed/' + name)
        self.assertTrue(inspect_runtime(importer)['ok'])
        self.assertNotIn('torch', names)
        self.assertNotIn('onnxruntime', names)

    def test_current_runtime_works_without_user_packages_or_pythonpath(self):
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT / 'nonexistent-user-path')
        env['PYTHONUSERBASE'] = str(ROOT / 'nonexistent-user-base')
        run = subprocess.run([sys.executable, '-I', str(ROOT / 'tools/check_startup.py')],
            capture_output=True, text=True, encoding='utf-8', timeout=20, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout)
        self.assertTrue(result['isolated'])
        self.assertTrue(result['ok'])
        self.assertNotIn('LocalCache', result['modules']['cv2'])


if __name__ == '__main__':
    unittest.main()
