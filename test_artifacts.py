import hashlib
import io
import json
from pathlib import Path
import tempfile
import shutil
import uuid
from contextlib import redirect_stdout
import unittest
import zipfile

from artifacts import ROOT, verify, valid_name, package, BUNDLE_FILES


class ArtifactTests(unittest.TestCase):
    def test_real_bundle(self):
        self.assertTrue(verify(ROOT / "evidence/queuepilot-evidence.zip")["valid"])

    def altered(self, mutation):
        with zipfile.ZipFile(ROOT / "evidence/queuepilot-evidence.zip") as source:
            files = {n: source.read(n) for n in source.namelist()}
        mutation(files)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        output.seek(0)
        return output

    def test_changed_file(self):
        with self.assertRaises(ValueError):
            verify(
                self.altered(lambda files: files.update({"protocol.json": b"changed"}))
            )

    def test_extra_file(self):
        with self.assertRaises(ValueError):
            verify(
                self.altered(lambda files: files.update({"unexpected.txt": b"extra"}))
            )

    def test_missing_file(self):
        with self.assertRaises(ValueError):
            verify(self.altered(lambda files: files.pop("models/candidate.npz")))

    def test_unsafe_archive_path(self):
        with self.assertRaises(ValueError):
            verify(self.altered(lambda files: files.update({"../escape": b"no"})))

    def test_reject_duplicate_manifest_key(self):
        with self.assertRaises(ValueError):
            verify(
                self.altered(
                    lambda files: files.update(
                        {"manifest.json": b'{"sha256":{},"sha256":{}}'}
                    )
                )
            )

    def test_no_runtime_data_in_public_bundle(self):
        with zipfile.ZipFile(ROOT / "evidence/queuepilot-evidence.zip") as archive:
            self.assertFalse(
                any(
                    "runtime/" in name or name.endswith(".sqlite3")
                    for name in archive.namelist()
                )
            )

    def test_invalid_names(self):
        for name in [
            "/absolute",
            "../x",
            "a/../b",
            "a//b",
            "a\\b",
            "C:evil",
            "a\x00b",
            "./a",
        ]:
            self.assertFalse(valid_name(name), name)

    def test_export_allowlist_excludes_unrelated_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canary = uuid.uuid4().hex
            for name in BUNDLE_FILES:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
            for name in (
                "credentials.json",
                "data/private-customers.csv",
                "web/debug.log",
            ):
                (root / name).write_text(canary)
            with redirect_stdout(io.StringIO()):
                package(root)
            with zipfile.ZipFile(root / "evidence/queuepilot-evidence.zip") as archive:
                self.assertEqual(
                    set(archive.namelist()) - {"manifest.json"}, set(BUNDLE_FILES)
                )
                self.assertFalse(
                    any(canary.encode() in archive.read(n) for n in archive.namelist())
                )


if __name__ == "__main__":
    unittest.main()
