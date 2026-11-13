"""Build a dated workspace ZIP and advance the plugin version."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "plugin.json"
GRAY_VERSION = ROOT / "scripts" / "gray_release_version.txt"
RELEASE_HISTORY = ROOT / "scripts" / "package_release_history.json"
PACKAGE_DIRS = ("board_core", "hooks", "skills", "agents", "workflows")
GRAY_SUFFIX = "_Gray"


def manifest_json(manifest):
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def add_gray_suffix(name):
    return name if name.endswith(GRAY_SUFFIX) else name + GRAY_SUFFIX


def raise_walk_error(error):
    raise error


def write_package(temporary_zip, destination, manifest):
    with ZipFile(temporary_zip, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", manifest_json(manifest))
        for item in sorted(ROOT.glob("*.py")) + sorted(ROOT.glob("*.json")):
            if item.is_file() and item != MANIFEST:
                archive.write(item, item.name)

        for package_dir in PACKAGE_DIRS:
            root = ROOT / package_dir
            if not root.is_dir():
                continue
            for directory, subdirs, files in os.walk(root, onerror=raise_walk_error):
                subdirs[:] = sorted(name for name in subdirs if name != "__pycache__")
                directory = Path(directory)
                archive.write(directory, directory.relative_to(ROOT))
                for name in sorted(files):
                    path = directory / name
                    if name == ".DS_Store" or path.suffix == ".pyc":
                        continue
                    if path not in (temporary_zip, destination):
                        archive.write(path, path.relative_to(ROOT))


def next_version(version, last_release, today):
    parts = version.split(".")
    major, minor, patch = parts[:3]
    if last_release == {"version": version, "date": today}:
        revision = int(parts[3]) if len(parts) == 4 else 0
        return f"{major}.{minor}.{patch}.{revision + 1}"
    return f"{major}.{minor}.{int(patch) + 1}"


def build_package(output, gray_release, gray_suffix):
    now = datetime.now()
    today = now.date().isoformat()
    channel = "gray" if gray_release else "regular"
    manifest_bytes = MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)
    version_file = GRAY_VERSION if gray_release else MANIFEST
    original = GRAY_VERSION.read_bytes() if gray_release else manifest_bytes
    version = original.decode("utf-8").strip() if gray_release else manifest.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2,3}", version):
        raise ValueError(f"{version_file.name} version must use major.minor.patch[.revision].")
    original_history = RELEASE_HISTORY.read_bytes()
    history = json.loads(original_history)
    version = next_version(version, history.get(channel), today)
    history[channel] = {"version": version, "date": today}
    manifest["version"] = version
    updated = (version + "\n" if gray_release else manifest_json(manifest)).encode("utf-8")
    if gray_suffix:
        manifest["name"] = add_gray_suffix(manifest["name"])

    stem = output.stem
    if gray_suffix and stem.endswith(GRAY_SUFFIX):
        stem = stem[:-len(GRAY_SUFFIX)]
    stem = f"{stem}_{version}_{now:%Y%m%d_%H%M%S_%f}"
    if gray_suffix:
        stem = add_gray_suffix(stem)
    destination = output.with_name(stem + output.suffix)
    if destination.exists():
        raise FileExistsError(f"Package already exists: {destination}")

    with tempfile.TemporaryDirectory(prefix=".package-workspace-", dir=ROOT) as staging:
        staging = Path(staging)
        state_updates = (
            (version_file, updated, original),
            (RELEASE_HISTORY, manifest_json(history).encode("utf-8"), original_history),
        )
        for path, contents, previous in state_updates:
            mode = stat.S_IMODE(path.stat().st_mode)
            for filename, data in ((path.name, contents), (path.name + ".original", previous)):
                staged_file = staging / filename
                staged_file.write_bytes(data)
                staged_file.chmod(mode)
        with tempfile.NamedTemporaryFile(
            prefix=".package-workspace-", suffix=".zip", dir=destination.parent, delete=False
        ) as handle:
            temporary_zip = Path(handle.name)
        replaced = []
        try:
            write_package(temporary_zip, destination, manifest)
            for path, _, _ in state_updates:
                (staging / path.name).replace(path)
                replaced.append(path)
            temporary_zip.replace(destination)
        except OSError:
            for path in reversed(replaced):
                (staging / (path.name + ".original")).replace(path)
            raise
        finally:
            temporary_zip.unlink(missing_ok=True)
    return destination, version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output", nargs="?", default="autobiz_kanban_workspace.zip",
        help="output ZIP base name or path, before adding the version and timestamp",
    )
    parser.add_argument("--gray-release", action="store_true", help="use the gray release version")
    parser.add_argument("--gray", dest="gray_suffix", action="store_true", help="add the _Gray suffix")
    parser.add_argument("--no-gray", dest="gray_suffix", action="store_false", help="omit the _Gray suffix")
    args = parser.parse_args()

    output = ROOT / args.output
    if output.suffix.lower() != ".zip":
        output = output.with_name(output.name + ".zip")
    output = output.parent.resolve() / output.name

    lock = ROOT / ".package-workspace.lock"
    try:
        try:
            lock.mkdir()
        except FileExistsError:
            raise ValueError(f"Packaging lock already exists: {lock}") from None
        try:
            destination, version = build_package(
                output, args.gray_release or args.gray_suffix, args.gray_suffix
            )
        finally:
            lock.rmdir()
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"Created {destination} (version {version})")


if __name__ == "__main__":
    main()
