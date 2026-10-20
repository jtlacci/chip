#!/usr/bin/env python3
"""Check that one change belongs to one loop. Standard library only.

Every changed file gets a class from the evaluation policy on the BASE commit,
so a change cannot widen its own permissions:

- ``code``: a code-loop surface of its brick (the dev agent's files)
- ``evaluation``: an evaluation-loop surface of its brick (the eval agent's files)
- ``human``: everything else, including policy, data.lock, contracts, a brick
  that does not exist on the base yet, and every file outside a brick

A change may contain code files or evaluation files, never both. Human files
are allowed in any change and listed, because CODEOWNERS decides who approves
them.

Usage:
    python3 tools/check_change.py --base <sha> --head <sha>
    python3 tools/check_change.py            # working tree against HEAD
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import policy as eval_policy  # type: ignore[no-redef]
else:
    from . import policy as eval_policy

EXIT_OK, EXIT_MIXED, EXIT_ERROR = 0, 1, 2


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def changed_files(root: Path, base: str | None, head: str | None) -> list[str]:
    # --no-renames: a rename is a deletion and an addition, and both paths count.
    # Otherwise `git mv contract.py src/x.py` would show only the code path.
    # -z: names exactly as stored. Without it Git C-quotes non-ASCII names
    # ("bricks/x/src/donn\303\251es.py"), and the quoted name matches no brick.
    if base and head:
        out = git(root, "diff", "--name-only", "-z", "--no-renames", f"{base}...{head}")
    else:
        out = git(root, "diff", "--name-only", "-z", "--no-renames", "HEAD")
        out += git(root, "ls-files", "-z", "--others", "--exclude-standard")
    return sorted({name for name in out.split("\0") if name})


def base_policy(root: Path, base: str, brick: str) -> dict | None:
    try:
        text = git(root, "show", f"{base}:bricks/{brick}/{eval_policy.POLICY_PATH}")
    except subprocess.CalledProcessError:
        return None
    try:
        policy = eval_policy.load(text)
    except ValueError:
        return None
    return None if eval_policy.check(policy) else policy


def classify_change(root: Path, base: str, files: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Each file with its class and the reason, grouped by class."""
    groups: dict[str, list[tuple[str, str]]] = {eval_policy.CODE: [], eval_policy.EVALUATION: [], eval_policy.HUMAN: []}
    policies: dict[str, dict | None] = {}
    for path in files:
        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "bricks" or parts[1].startswith(("_", ".")):
            groups[eval_policy.HUMAN].append((path, "outside any brick"))
            continue
        brick, inside = parts[1], "/".join(parts[2:])
        if brick not in policies:
            policies[brick] = base_policy(root, base, brick)
        policy = policies[brick]
        if policy is None:
            groups[eval_policy.HUMAN].append((path, "no valid policy on the base: a new or broken brick"))
            continue
        label = eval_policy.classify(policy, inside)
        groups[label].append((path, f"{label} surface of {brick}" if label != eval_policy.HUMAN else "human-only"))
    return groups


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--base")
    parser.add_argument("--head")
    args = parser.parse_args()
    if bool(args.base) != bool(args.head):
        print("set both --base and --head, or neither", file=sys.stderr)
        return EXIT_ERROR
    root = args.root.resolve()
    try:
        files = changed_files(root, args.base, args.head)
        groups = classify_change(root, args.base or "HEAD", files)
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {exc.stderr.strip()}", file=sys.stderr)
        return EXIT_ERROR

    for label in (eval_policy.CODE, eval_policy.EVALUATION, eval_policy.HUMAN):
        if groups[label]:
            print(f"{label}:")
            for path, reason in groups[label]:
                print(f"  {path}  ({reason})")
    if groups[eval_policy.HUMAN]:
        print("human files need approval from CODEOWNERS")
    if groups[eval_policy.CODE] and groups[eval_policy.EVALUATION]:
        print("FAILED: one change may contain code-loop files or evaluation-loop files, never both")
        return EXIT_MIXED
    print("change check passed")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
