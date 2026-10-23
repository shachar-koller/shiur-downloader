#!/usr/bin/env python3
"""Print a proposed archive organization as JSON. Never modifies Drive files."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath

from categories import DEFAULT_CONFIG, Routing, load_routing
from sync import list_remote_filenames


ARCHIVE_NAME = re.compile(r"^(\d+) - (.+)\.mp3$", re.IGNORECASE)


def plan_moves(paths: list[str], routing: Routing) -> dict:
    moves, skipped, conflicts = [], [], []
    identities = Counter(
        match.group(1) for path in paths
        if (match := ARCHIVE_NAME.fullmatch(PurePosixPath(path).name))
    )
    for path in sorted(paths):
        source = PurePosixPath(path)
        match = ARCHIVE_NAME.fullmatch(source.name)
        if not match or source.is_absolute() or ".." in source.parts:
            skipped.append({"path": path, "reason": "Unrecognized archive path"})
            continue
        if len(source.parts) > 2 or (len(source.parts) == 2 and source.parts[0] not in ("Iyun", "Other")):
            skipped.append({"path": path, "reason": "Outside the flat archive and category folders"})
            continue
        shiur_id, title = match.groups()
        target = f"{routing.category(shiur_id, title)}/{source.name}"
        if identities[shiur_id] > 1:
            conflicts.append({"path": path, "reason": f"Multiple archived files have shiur ID {shiur_id}"})
        elif path == target:
            skipped.append({"path": path, "reason": "Already in the correct category"})
        else:
            moves.append({"shiur_id": shiur_id, "source": path, "target": target})
    return {"moves": moves, "skipped": skipped, "conflicts": conflicts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teacher-id", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--destination", help="rclone destination to list recursively (read only)")
    source.add_argument("--inventory", type=Path, help="Saved rclone lsf --files-only --recursive output")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    routing = load_routing(args.teacher_id, args.config)
    paths = (args.inventory.read_text(encoding="utf-8").splitlines() if args.inventory
             else list_remote_filenames(args.destination))
    report = plan_moves([path for path in paths if path], routing)
    print(json.dumps({"dry_run": True, "destination": args.destination, **report}, ensure_ascii=False, indent=2))
    return 1 if report["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
