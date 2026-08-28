#!/usr/bin/env python3
"""Conservative, rules-based organizer for common macOS document folders."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tomllib
from typing import Any, Callable, Iterable

from llm_classifier import VisionClassifier


TYPE_NAMES = {
    ".pdf": "PDF",
    ".txt": "Text", ".md": "Text", ".rtf": "Text",
    ".doc": "Documents", ".docx": "Documents", ".pages": "Documents",
    ".xls": "Spreadsheets", ".xlsx": "Spreadsheets", ".csv": "Spreadsheets",
    ".numbers": "Spreadsheets",
    ".ppt": "Presentations", ".pptx": "Presentations", ".key": "Presentations",
    ".jpg": "Images", ".jpeg": "Images", ".png": "Images", ".heic": "Images",
    ".tif": "Images", ".tiff": "Images",
    ".zip": "Archives",
}


def expanded(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    required = {"scan_roots", "organized_root"}
    missing = required - config.keys()
    if missing:
        raise ValueError(f"Missing config keys: {', '.join(sorted(missing))}")
    return config


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def spotlight_text(path: Path) -> str:
    """Return a small amount of indexed text without reading document contents."""
    if path.suffix.lower() != ".pdf" or sys.platform != "darwin":
        return ""
    try:
        result = subprocess.run(
            ["mdls", "-raw", "-name", "kMDItemTextContent", str(path)],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    value = result.stdout.strip()
    return "" if value in {"", "(null)"} else value[:20_000]


def normalized_context(path: Path) -> str:
    raw = " ".join([path.stem, *path.parent.parts[-3:]])
    raw = re.sub(r"[_\-.]+", " ", raw).lower()
    return f"{raw} {spotlight_text(path).lower()}"


def classify(path: Path, rules: dict[str, Any]) -> tuple[str, str]:
    context = normalized_context(path)
    for category, settings in rules.items():
        for keyword in settings.get("keywords", []):
            if str(keyword).lower() in context:
                return category, f'keyword "{keyword}"'
    kind = TYPE_NAMES.get(path.suffix.lower(), "Other")
    return f"By Type/{kind}", "file type"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def unique_destination(candidate: Path, source: Path) -> tuple[Path, bool]:
    """Return destination and whether an identical file already occupies it."""
    if not candidate.exists():
        return candidate, False
    try:
        if candidate.stat().st_size == source.stat().st_size and sha256(candidate) == sha256(source):
            return candidate, True
    except OSError:
        pass
    for number in range(2, 10_000):
        alternate = candidate.with_name(f"{candidate.stem}-{number}{candidate.suffix}")
        if not alternate.exists():
            return alternate, False
    raise RuntimeError(f"Too many naming collisions for {candidate}")


def vacant_destination(candidate: Path) -> Path:
    """Return an unused path without treating matching content as reusable."""
    if not candidate.exists():
        return candidate
    for number in range(2, 10_000):
        alternate = candidate.with_name(f"{candidate.stem}-{number}{candidate.suffix}")
        if not alternate.exists():
            return alternate
    raise RuntimeError(f"Too many naming collisions for {candidate}")


def iter_files(
    roots: Iterable[Path], organized_root: Path, allowed: set[str], cutoff: float,
    excluded: Iterable[Path] = (),
) -> Iterable[Path]:
    excluded = tuple(excluded)

    def is_excluded(path: Path) -> bool:
        return is_relative_to(path, organized_root) or any(
            is_relative_to(path, excluded_root) for excluded_root in excluded
        )

    seen: set[tuple[int, int]] = set()
    for root in roots:
        if not root.is_dir() or is_excluded(root):
            continue
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(directory)
            dirnames[:] = [
                name for name in dirnames
                if not name.startswith(".")
                and not is_excluded(current / name)
                and not (current / name).is_symlink()
                and (current / name).suffix.lower() not in {
                    ".app", ".photoslibrary", ".photolibrary", ".pages", ".numbers",
                    ".key", ".rtfd",
                }
            ]
            for name in filenames:
                if name.startswith(".") or name.endswith((".download", ".crdownload", ".part")):
                    continue
                path = current / name
                try:
                    stat = path.lstat()
                except OSError:
                    continue
                if path.is_symlink() or not path.is_file() or stat.st_mtime > cutoff:
                    continue
                if allowed and path.suffix.lower().lstrip(".") not in allowed:
                    continue
                identity = (stat.st_dev, stat.st_ino)
                if identity not in seen:
                    seen.add(identity)
                    yield path


def action_for(
    path: Path, organized_root: Path, rules: dict[str, Any],
    vision: VisionClassifier | None = None,
    progress: Callable[[Path], None] | None = None,
) -> dict[str, Any]:
    category, reason = classify(path, rules)
    if vision:
        if progress and vision.should_classify(path, category):
            progress(path)
        category, reason = vision.classify(path, category, reason)
    year = time.strftime("%Y", time.localtime(path.stat().st_mtime))
    proposed, duplicate = unique_destination(organized_root / category / year / path.name, path)
    if duplicate:
        proposed = vacant_destination(
            organized_root / "_Review" / "Duplicates" / year / path.name
        )
        reason = "identical destination already exists"
    return {
        "source": str(path),
        "destination": str(proposed),
        "category": "_Review/Duplicates" if duplicate else category,
        "reason": reason,
    }


def append_log(action: dict[str, Any]) -> None:
    log_path = Path.home() / "Library" / "Logs" / "KeepMacsOrganized" / "actions.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **action}
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


@contextmanager
def apply_lock() -> Iterable[None]:
    """Prevent two local scheduler/manual runs from moving the same files."""
    lock_path = (
        Path.home() / "Library" / "Application Support" / "KeepMacsOrganized" / "organizer.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another organizer run is already active") from error
        yield


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.toml"))
    parser.add_argument("--apply", action="store_true", help="Move files; default is preview only")
    parser.add_argument("--json", action="store_true", help="Print actions as JSON lines")
    parser.add_argument("--min-age-hours", type=float, help="Override config age threshold")
    parser.add_argument("--no-vision", action="store_true", help="Skip LAN vision classification")
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config.resolve())
        roots = [expanded(value) for value in config["scan_roots"]]
        organized_root = expanded(config["organized_root"])
        age = args.min_age_hours if args.min_age_hours is not None else float(config.get("min_age_hours", 24))
        allowed = {str(ext).lower().lstrip(".") for ext in config.get("allowed_extensions", [])}
        rules = config.get("rules", {})
        vision_settings = config.get("vision_llm", {})
        vision = None
        if not getattr(args, "no_vision", False) and str(vision_settings.get("mode", "off")).lower() != "off":
            vision = VisionClassifier(vision_settings, [*rules.keys(), "By Type/PDF"])
        # Always protect the checkout itself, wherever it is cloned.
        excluded = [Path(__file__).resolve().parent]
        excluded.extend(expanded(value) for value in config.get("exclude_paths", []))
        cutoff = time.time() - age * 3600
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if not args.json:
        vision_status = "enabled" if vision else "disabled"
        print(
            f"Scanning {len(roots)} folder(s); vision {vision_status}; "
            f"files must be at least {age:g} hour(s) old.",
            flush=True,
        )

    def show_progress(path: Path) -> None:
        print(f"ANALYZING PDF: {path}", file=sys.stderr, flush=True)

    failures = 0
    action_count = 0
    paths = iter_files(roots, organized_root, allowed, cutoff, excluded)
    for path in paths:
        action = action_for(path, organized_root, rules, vision, show_progress)
        action_count += 1
        if args.json:
            print(
                json.dumps({"mode": "apply" if args.apply else "preview", **action}, ensure_ascii=False),
                flush=True,
            )
        else:
            verb = "MOVE" if args.apply else "WOULD MOVE"
            print(
                f"{verb}: {action['source']}\n"
                f"       -> {action['destination']} ({action['reason']})",
                flush=True,
            )
        if args.apply:
            try:
                destination = Path(action["destination"])
                if destination.exists():
                    destination = vacant_destination(destination)
                    action["destination"] = str(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(action["source"], destination)
                append_log(action)
            except OSError as error:
                failures += 1
                print(f"error moving {action['source']}: {error}", file=sys.stderr)

    label = "moved" if args.apply else "proposed"
    if not args.json:
        print(f"\n{action_count - failures} file(s) {label}; {failures} failure(s).")
    return 1 if failures else 0


def main() -> int:
    args = parse_args()
    if not args.apply:
        return run(args)
    try:
        with apply_lock():
            return run(args)
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
