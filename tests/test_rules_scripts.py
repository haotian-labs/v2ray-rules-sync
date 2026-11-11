"""Offline regression tests: python3 -m unittest discover -s tests -v."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = ("download-rules.sh", "sync-rules.sh")
FILES = ("geosite_cn.txt", "geosite_geolocation-!cn.txt", "geoip_cn.txt")
MOCK_TOOL = r'''
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import unquote

args = sys.argv[1:]
tool = Path(sys.argv[0]).name
with open(os.environ["CALL_LOG"], "a") as log:
    log.write(json.dumps([tool, args]) + "\n")
if tool == "v2dat":
    kind = args[1]
    rule = args[args.index("-f") + 1]
    name = f"{kind}_{rule}.txt"
    if os.environ.get("FAIL_UNPACK") == rule:
        sys.exit(2)
    if os.environ.get("OMIT_RULE") != name:
        output = Path(args[args.index("-o") + 1]) / name
        output.write_text("" if os.environ.get("EMPTY_RULE") == name else f"rule:{name}\n")
else:
    output = Path(args[args.index("-o" if tool == "curl" else "-O") + 1])
    name = unquote(args[-1].rsplit("/", 1)[-1])
    if os.environ.get("BLOCK_READY"):
        Path(os.environ["BLOCK_READY"]).touch()
        deadline = time.monotonic() + 10
        while not Path(os.environ["BLOCK_RELEASE"]).exists():
            if time.monotonic() > deadline:
                sys.exit(3)
            time.sleep(0.01)
    output.write_text("" if os.environ.get("EMPTY_DOWNLOAD") == name else f"rule:{name}\n")
    if os.environ.get("FAIL_DOWNLOAD") == name:
        sys.exit(1)
'''


class RulesScriptsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rules scripts ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("date", "mkdir", "rm", "rmdir", "mktemp", "chmod", "mv", "cmp"):
            (self.bin / name).symlink_to(shutil.which(name))
        self.rules = self.root / "rules data"
        self.rules.mkdir()
        self.log = self.root / "calls.jsonl"
        self.env = dict(os.environ, PATH=str(self.bin), RULES_DIR="rules data", CALL_LOG=str(self.log))
        self.use_downloader("curl")
        self.write_mock("v2dat")

    def write_mock(self, name):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + MOCK_TOOL)
        path.chmod(0o755)

    def use_downloader(self, name):
        for tool in ("curl", "wget"):
            (self.bin / tool).unlink(missing_ok=True)
        self.write_mock(name)

    def seed(self):
        for name in FILES:
            (self.rules / name).write_text(f"old:{name}\n")

    def run_script(self, script, **env):
        return subprocess.run(
            ["/bin/bash", str(REPO / script)], cwd=self.root,
            env=dict(self.env, **env), capture_output=True, text=True, timeout=10,
        )

    def assert_clean(self):
        self.assertEqual([], list(self.rules.glob(".rules-*")))

    def assert_old_rules(self):
        for name in FILES:
            self.assertEqual(f"old:{name}\n", (self.rules / name).read_text())
        self.assert_clean()

    def test_success_and_unchanged_files_with_both_downloaders(self):
        for script in SCRIPTS:
            for downloader in ("curl", "wget"):
                with self.subTest(script=script, downloader=downloader):
                    self.use_downloader(downloader)
                    self.seed()
                    result = self.run_script(script, BASE_URL="https://example.test/release/")
                    self.assertEqual(0, result.returncode, result.stderr)
                    for name in FILES:
                        path = self.rules / name
                        self.assertEqual(f"rule:{name}\n", path.read_text())
                        self.assertEqual(0o644, path.stat().st_mode & 0o777)
                    before = {name: (self.rules / name).stat() for name in FILES}
                    (self.rules / FILES[0]).chmod(0o600)
                    result = self.run_script(script)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertIn("更新 0 个文件", result.stdout)
                    for name in FILES:
                        after = (self.rules / name).stat()
                        self.assertEqual(before[name].st_mtime_ns, after.st_mtime_ns)
                        self.assertEqual(before[name].st_ino, after.st_ino)
                    self.assertEqual(0o600, (self.rules / FILES[0]).stat().st_mode & 0o777)
                    self.assert_clean()
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        for tool in ("curl", "wget"):
            urls = [args[-1] for name, args in calls if name == tool]
            self.assertIn("https://example.test/release/geosite_geolocation-%21cn.txt", urls)

    def test_open_readers_keep_complete_old_files_during_replacement(self):
        for script in SCRIPTS:
            with self.subTest(script=script), ExitStack() as stack:
                self.seed()
                readers = [stack.enter_context((self.rules / name).open()) for name in FILES]
                result = self.run_script(script)
                self.assertEqual(0, result.returncode, result.stderr)
                for name, reader in zip(FILES, readers):
                    self.assertEqual(f"old:{name}\n", reader.read())
                    self.assertEqual(f"rule:{name}\n", (self.rules / name).read_text())
                self.assert_clean()

    def test_download_failure_and_empty_download_preserve_rules(self):
        for script in SCRIPTS:
            filename = "geoip.dat" if script == "sync-rules.sh" else FILES[-1]
            for downloader in ("curl", "wget"):
                for failure in ("FAIL_DOWNLOAD", "EMPTY_DOWNLOAD"):
                    with self.subTest(script=script, downloader=downloader, failure=failure):
                        self.use_downloader(downloader)
                        self.seed()
                        result = self.run_script(script, **{failure: filename})
                        self.assertNotEqual(0, result.returncode)
                        self.assertIn("ERROR:", result.stderr)
                        self.assert_old_rules()

    def test_unpack_failure_and_invalid_output_preserve_rules(self):
        for failure in ({"FAIL_UNPACK": "cn"}, {"OMIT_RULE": FILES[-1]}, {"EMPTY_RULE": FILES[-1]}):
            with self.subTest(failure=failure):
                self.seed()
                result = self.run_script("sync-rules.sh", **failure)
                self.assertNotEqual(0, result.returncode)
                self.assert_old_rules()

    def test_existing_lock_is_preserved(self):
        lock = self.rules / ".rules-update.lock"
        lock.mkdir()
        for script in SCRIPTS:
            with self.subTest(script=script):
                result = self.run_script(script)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("无法获取更新锁", result.stderr)
                self.assertTrue(lock.is_dir())
        self.assertFalse(self.log.exists())

    def test_directory_target_is_rejected_before_any_file_changes(self):
        for script in SCRIPTS:
            with self.subTest(script=script):
                self.seed()
                target = self.rules / FILES[-1]
                target.unlink()
                target.mkdir()
                result = self.run_script(script)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("目标路径是目录", result.stderr)
                for name in FILES[:-1]:
                    self.assertEqual(f"old:{name}\n", (self.rules / name).read_text())
                self.assertEqual([], list(target.iterdir()))
                self.assert_clean()
                target.rmdir()

    def test_missing_dependency_does_not_create_lock(self):
        for script in SCRIPTS:
            with self.subTest(script=script):
                (self.bin / "cmp").unlink()
                result = self.run_script(script)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("未找到命令: cmp", result.stderr)
                self.assert_clean()
                (self.bin / "cmp").symlink_to(shutil.which("cmp"))

    def start_blocked(self, script):
        ready, release = self.root / "ready", self.root / "release"
        process = subprocess.Popen(
            ["/bin/bash", str(REPO / script)], cwd=self.root,
            env=dict(self.env, BLOCK_READY=str(ready), BLOCK_RELEASE=str(release)),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        def finish():
            release.touch()
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=10)

        self.addCleanup(finish)
        deadline = time.monotonic() + 5
        while not ready.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                self.fail("Mock download did not start")
            time.sleep(0.01)
        return process, release

    def test_scripts_share_lock(self):
        process, release = self.start_blocked("download-rules.sh")
        result = self.run_script("sync-rules.sh")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("无法获取更新锁", result.stderr)
        self.assertTrue((self.rules / ".rules-update.lock").is_dir())
        release.touch()
        _, stderr = process.communicate(timeout=10)
        self.assertEqual(0, process.returncode, stderr)
        self.assert_clean()

    def test_term_cleans_up_and_preserves_rules(self):
        for script in SCRIPTS:
            with self.subTest(script=script):
                for name in ("ready", "release"):
                    (self.root / name).unlink(missing_ok=True)
                self.seed()
                process, release = self.start_blocked(script)
                process.send_signal(signal.SIGTERM)
                release.touch()
                process.communicate(timeout=10)
                self.assertEqual(143, process.returncode)
                self.assert_old_rules()


if __name__ == "__main__":
    unittest.main()
