"""Validate commit messages against the Conventional Commits rules in CLAUDE.md.

Usage:
    check_commit_msg.py --file .git/COMMIT_EDITMSG
    check_commit_msg.py --range origin/main..HEAD
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TYPES = ("feat", "fix", "refactor", "perf", "test", "docs", "build", "ci", "chore", "revert")
MAX_SUBJECT = 72
HEADER = re.compile(rf"^(?:{'|'.join(TYPES)})(?:\([a-z0-9-]+\))?!?: [^A-Z].*[^.]$")
FORBIDDEN = re.compile(r"co-authored-by: claude|generated with \[?claude", re.IGNORECASE)
SKIPPED_PREFIXES = ("Merge ", "fixup! ", "squash! ")


def problems(message: str) -> list[str]:
    subject = message.strip().splitlines()[0] if message.strip() else ""
    if subject.startswith(SKIPPED_PREFIXES):
        return []
    found = []
    if not HEADER.match(subject):
        found.append(
            f"subject must be 'type(scope): lowercase summary' with no trailing period: {subject!r}"
        )
    if len(subject) > MAX_SUBJECT:
        found.append(f"subject is {len(subject)} chars, max {MAX_SUBJECT}")
    if FORBIDDEN.search(message):
        found.append("AI attribution lines are not allowed")
    return found


def messages_in_range(revision_range: str) -> dict[str, str]:
    output = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "log", "--format=%H%x00%B%x01", revision_range],  # noqa: S607 - git from PATH
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    commits = {}
    for record in filter(str.strip, output.split("\x01")):
        sha, _, body = record.strip().partition("\x00")
        commits[sha[:10]] = body
    return commits


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path)
    source.add_argument("--range")
    args = parser.parse_args()

    if args.file:
        commits = {"commit": args.file.read_text(encoding="utf-8")}
    else:
        commits = messages_in_range(args.range)

    failed = False
    for ref, message in commits.items():
        for problem in problems(message):
            failed = True
            sys.stderr.write(f"{ref}: {problem}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
