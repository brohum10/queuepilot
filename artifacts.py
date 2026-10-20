"""Reproducible evidence packaging and bounded, non-executing ZIP verification."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

ROOT = Path(__file__).resolve().parent
MAX_EXPANDED = 50_000_000
BUNDLE_FILES = (
    "artifacts.py",
    "diagnostics.py",
    "evaldelta.py",
    "routing.py",
    "server.py",
    "store.py",
    "text_model.py",
    "train.py",
    "test_artifacts.py",
    "test_diagnostics.py",
    "test_models.py",
    "test_routing.py",
    "test_server.py",
    "test_store.py",
    "protocol.json",
    "requirements.txt",
    "LICENSE",
    "LICENSE-EVALDELTA",
    "data/LICENSE-BANKING77",
    "data/README.md",
    "data/categories.json",
    "data/challenge.json",
    "data/sample-import.csv",
    "data/train.csv",
    "data/test.csv",
    "models/baseline.json",
    "models/baseline.npz",
    "models/candidate.json",
    "models/candidate.npz",
    "web/index.html",
    "web/style.css",
    "web/app.js",
    "evidence/report.json",
    "evidence/predictions.json",
    "evidence/splits.json",
    "evidence/baseline.jsonl",
    "evidence/candidate.jsonl",
)


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def valid_name(name):
    return (
        isinstance(name, str)
        and not any(c in name for c in ("\\", "\x00", ":"))
        and not PurePosixPath(name).is_absolute()
        and all(p not in ("..", ".", "") for p in name.split("/"))
    )


def verify(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        names = [m.filename for m in members]
        if (
            len(names) > 100
            or len(set(names)) != len(names)
            or sum(m.file_size for m in members) > MAX_EXPANDED
        ):
            raise ValueError("Duplicate, too many, or oversized members.")
        for item in members:
            if (
                not valid_name(item.filename)
                or item.is_dir()
                or stat.S_ISLNK(item.external_attr >> 16)
                or item.flag_bits & 1
            ):
                raise ValueError("Unsafe archive member.")
        if "manifest.json" not in names:
            raise ValueError("Missing manifest.")
        manifest = json.loads(
            archive.read("manifest.json"), object_pairs_hook=strict_object
        )
        hashes = manifest.get("sha256")
        if not isinstance(hashes, dict) or set(hashes) != set(names) - {
            "manifest.json"
        }:
            raise ValueError("Missing or extra evidence files.")
        for name, expected in hashes.items():
            if (
                not valid_name(name)
                or not isinstance(expected, str)
                or len(expected) != 64
                or hashlib.sha256(archive.read(name)).hexdigest() != expected
            ):
                raise ValueError("Checksum mismatch: " + name)
        required = {
            "protocol.json",
            "models/baseline.json",
            "models/baseline.npz",
            "models/candidate.json",
            "models/candidate.npz",
            "evidence/report.json",
            "evidence/predictions.json",
            "evidence/splits.json",
            "evidence/baseline.jsonl",
            "evidence/candidate.jsonl",
        }
        if not required <= set(hashes):
            raise ValueError("Core evidence is incomplete.")
        return {
            "valid": True,
            "files_checked": len(hashes),
            "scope": "Integrity relative to an unsigned manifest; not proof of authenticity or scientific validity.",
        }


def package(root=ROOT):
    root = Path(root)
    # Closed allowlist: a future credentials.json, customer CSV or debug log must
    # never become public merely because it was placed beside project sources.
    files = [root / name for name in sorted(BUNDLE_FILES)]
    if any(not p.is_file() or p.is_symlink() for p in files):
        raise ValueError("Reference files must exist and must not be symlinks.")
    payload = {p.relative_to(root).as_posix(): p.read_bytes() for p in files}
    manifest = {
        "version": 1,
        "scope": "Public reference experiment only. Runtime imports and review notes are excluded.",
        "sha256": {n: hashlib.sha256(data).hexdigest() for n, data in payload.items()},
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output = root / "evidence/queuepilot-evidence.zip"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in {**payload, "manifest.json": manifest_bytes}.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    result = verify(output)
    (root / "evidence/manifest.json").write_bytes(manifest_bytes)
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    try:
        if args.build:
            package()
        elif args.verify:
            print(json.dumps(verify(args.verify)))
        else:
            parser.error("Choose --build or --verify PATH.")
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as exc:
        parser.exit(2, "Evidence invalid: " + str(exc) + "\n")
