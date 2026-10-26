#!/usr/bin/env python3
"""Declarative, idempotent Git patch manager for BusyBox upstream sources.

This module verifies and applies ordered unified diff patches from a local
``patches/`` directory against a target BusyBox source directory using
``git apply``. If a patch has already been applied (detected via reverse
check ``git apply --reverse --check``), it is skipped cleanly so repeated
CMake configure/build invocations remain strictly idempotent.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def _clean_git_env() -> dict[str, str]:
    """Return a copy of ``os.environ`` with repository-scoping ``GIT_*`` variables removed.

    When ``apply_patches`` or its unit tests run inside a ``git commit`` hook
    (such as ``pre-commit``), Git exports ``GIT_DIR``, ``GIT_WORK_TREE``, and
    ``GIT_INDEX_FILE`` pointing to the parent repository. Stripping those
    variables ensures ``git -C <dir>`` operates strictly on ``<dir>``.

    Returns:
        Sanitized environment dictionary safe for isolated ``git -C`` subprocesses.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def is_patch_applied(busybox_dir: Path, patch_file: Path) -> bool:
    """Check whether a unified diff patch is already applied to the target tree.

    Args:
        busybox_dir: Path to the BusyBox source tree root.
        patch_file: Path to the ``.patch`` file to check in reverse.

    Returns:
        ``True`` if ``git apply --reverse --check`` succeeds, ``False`` otherwise.
    """
    result = subprocess.run(
        [
            "git",
            "-C",
            str(busybox_dir),
            "apply",
            "--reverse",
            "--check",
            "--ignore-space-change",
            "--ignore-whitespace",
            str(patch_file.resolve()),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_git_env(),
    )
    return result.returncode == 0


def can_apply_patch(busybox_dir: Path, patch_file: Path) -> bool:
    """Check whether a unified diff patch can be cleanly applied forward.

    Args:
        busybox_dir: Path to the BusyBox source tree root.
        patch_file: Path to the ``.patch`` file to validate.

    Returns:
        ``True`` if ``git apply --check`` succeeds, ``False`` otherwise.
    """
    result = subprocess.run(
        [
            "git",
            "-C",
            str(busybox_dir),
            "apply",
            "--check",
            "--ignore-space-change",
            "--ignore-whitespace",
            str(patch_file.resolve()),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_git_env(),
    )
    return result.returncode == 0


def apply_single_patch(busybox_dir: Path, patch_file: Path) -> bool:
    """Apply a single patch idempotently to the target BusyBox directory.

    Args:
        busybox_dir: Path to the BusyBox source tree root.
        patch_file: Path to the ``.patch`` file to apply.

    Returns:
        ``True`` if the patch was newly applied, or ``False`` if it was already
        present.

    Raises:
        RuntimeError: If the patch is neither already applied nor cleanly
            applicable.
    """
    if is_patch_applied(busybox_dir, patch_file):
        return False

    if not can_apply_patch(busybox_dir, patch_file):
        raise RuntimeError(f"Patch {patch_file.name} cannot be cleanly applied to {busybox_dir}")

    subprocess.run(
        [
            "git",
            "-C",
            str(busybox_dir),
            "apply",
            "--ignore-space-change",
            "--ignore-whitespace",
            str(patch_file.resolve()),
        ],
        check=True,
        env=_clean_git_env(),
    )
    return True


def apply_all_patches(busybox_dir: Path, patches_dir: Path) -> list[str]:
    """Apply all ``*.patch`` files in sorted order from ``patches_dir``.

    Args:
        busybox_dir: Path to the BusyBox source tree root.
        patches_dir: Directory containing ordered ``*.patch`` files.

    Returns:
        List of patch filenames that were newly applied during this run.

    Raises:
        FileNotFoundError: If ``busybox_dir`` or ``patches_dir`` does not exist.
    """
    if not busybox_dir.is_dir():
        raise FileNotFoundError(f"BusyBox directory not found: {busybox_dir}")
    if not patches_dir.is_dir():
        raise FileNotFoundError(f"Patches directory not found: {patches_dir}")

    applied: list[str] = []
    patch_files = sorted(patches_dir.glob("*.patch"))
    for patch_file in patch_files:
        if apply_single_patch(busybox_dir, patch_file):
            applied.append(patch_file.name)
    return applied


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the patch manager CLI.

    Args:
        argv: Optional sequence of CLI arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed ``argparse.Namespace`` containing ``busybox_dir``,
        ``patches_dir``, and ``stamp_file``.
    """
    parser = argparse.ArgumentParser(
        description="Idempotently apply BusyBox compatibility patches."
    )
    parser.add_argument(
        "busybox_dir",
        type=Path,
        help="Path to the BusyBox source tree",
    )
    parser.add_argument(
        "patches_dir",
        type=Path,
        help="Path to the directory containing .patch files",
    )
    parser.add_argument(
        "--stamp",
        dest="stamp_file",
        type=Path,
        default=None,
        help="Optional stamp file path to touch upon completion",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the patch manager entry point.

    Args:
        argv: Optional sequence of CLI arguments.

    Returns:
        Exit status code (``0`` on success, ``1`` on failure).
    """
    args = parse_args(argv)
    try:
        applied = apply_all_patches(args.busybox_dir, args.patches_dir)
    except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error applying patches: {exc}", file=sys.stderr)
        return 1

    if args.stamp_file is not None:
        args.stamp_file.parent.mkdir(parents=True, exist_ok=True)
        stamp_content = "\n".join(applied)
        if applied:
            stamp_content += "\n"
        args.stamp_file.write_text(stamp_content, encoding="utf-8")

    print(f"Verified patches in {args.patches_dir} ({len(applied)} newly applied).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
