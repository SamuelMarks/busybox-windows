#!/usr/bin/env python3
"""Update or add '% doc coverage' and '% test coverage' shields in README.md.

This module computes Python docstring coverage (via ``interrogate``) and unit
test line/branch coverage (via ``coverage`` / ``pytest``) and updates or inserts
the corresponding shields.io badges in ``README.md``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

DOC_BADGE_ALT = "Doc Coverage"
TEST_BADGE_ALT = "Test Coverage"

DOC_BADGE_LABEL = "doc%20coverage"
TEST_BADGE_LABEL = "test%20coverage"

DOC_RESULT_RE = re.compile(r"actual:\s*([\d.]+)%", re.IGNORECASE)
COV_TOTAL_RE = re.compile(r"^TOTAL\s+.*?\s([\d.]+)%\s*$", re.MULTILINE)


def get_shield_color(percentage: float) -> str:
    """Return the shields.io color corresponding to a coverage percentage.

    Args:
        percentage: Coverage percentage between 0.0 and 100.0.

    Returns:
        Shields.io color identifier string.
    """
    if percentage >= 95.0:
        return "brightgreen"
    if percentage >= 90.0:
        return "green"
    if percentage >= 80.0:
        return "yellowgreen"
    if percentage >= 70.0:
        return "yellow"
    if percentage >= 60.0:
        return "orange"
    return "red"


def format_percentage(percentage: float) -> str:
    """Format a coverage percentage concisely without trailing ``.0``.

    Args:
        percentage: Coverage percentage between 0.0 and 100.0.

    Returns:
        Formatted percentage string (e.g. ``"100"`` or ``"98.5"``).
    """
    rounded = round(percentage, 1)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.1f}"


def build_shield_markdown(badge_alt: str, badge_label: str, percentage: float) -> str:
    """Build a Markdown shields.io badge string for the given coverage metric.

    Args:
        badge_alt: Markdown image alt text (e.g. ``"Doc Coverage"``).
        badge_label: URL-encoded shield label (e.g. ``"doc%20coverage"``).
        percentage: Coverage percentage between 0.0 and 100.0.

    Returns:
        Formatted Markdown shield string.
    """
    pct_str = format_percentage(percentage)
    color = get_shield_color(percentage)
    return f"![{badge_alt}](https://img.shields.io/badge/{badge_label}-{pct_str}%25-{color})"


def compute_doc_coverage(repo_root: Path) -> float:
    """Compute docstring coverage percentage across ``scripts`` and ``tests``.

    Args:
        repo_root: Root directory of the repository.

    Returns:
        Docstring coverage percentage between 0.0 and 100.0.

    Raises:
        RuntimeError: If ``interrogate`` fails or its output cannot be parsed.
    """
    nl = chr(10)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "interrogate",
            "--fail-under=0",
            "-vv",
            "scripts",
            "tests",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = f"{result.stdout}{nl}{result.stderr}"
    match = DOC_RESULT_RE.search(combined)
    if match is None:
        raise RuntimeError(f"Unable to parse interrogate output: {combined.strip()}")
    return float(match.group(1))


def compute_test_coverage(repo_root: Path) -> float:
    """Compute unit test coverage percentage across ``scripts`` and ``tests``.

    Reads existing ``.coverage`` data via ``coverage report`` if present and
    valid; otherwise runs ``pytest --cov=scripts --cov=tests --cov-branch``.

    Args:
        repo_root: Root directory of the repository.

    Returns:
        Test coverage percentage between 0.0 and 100.0.

    Raises:
        RuntimeError: If coverage calculation fails or output cannot be parsed.
    """
    nl = chr(10)
    cov_file = repo_root / ".coverage"
    if cov_file.is_file():
        report_res = subprocess.run(
            [sys.executable, "-m", "coverage", "report"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if report_res.returncode == 0:
            match = COV_TOTAL_RE.search(report_res.stdout)
            if match is not None:
                return float(match.group(1))

    pytest_res = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--cov=scripts",
            "--cov=tests",
            "--cov-branch",
            "--cov-report=term",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = f"{pytest_res.stdout}{nl}{pytest_res.stderr}"
    match = COV_TOTAL_RE.search(combined)
    if match is None:
        raise RuntimeError(f"Unable to parse coverage output: {combined.strip()}")
    return float(match.group(1))


def update_or_insert_shield(
    readme_text: str,
    badge_alt: str,
    badge_label: str,
    percentage: float,
) -> tuple[str, bool]:
    """Update an existing coverage shield or insert a new one into ``README.md`` text.

    Args:
        readme_text: Full Markdown contents of ``README.md``.
        badge_alt: Markdown image alt text (e.g. ``"Doc Coverage"``).
        badge_label: URL-encoded shield label (e.g. ``"doc%20coverage"``).
        percentage: Coverage percentage between 0.0 and 100.0.

    Returns:
        Tuple of ``(updated_readme_text, was_modified)``.
    """
    new_badge = build_shield_markdown(badge_alt, badge_label, percentage)
    label_variants = (
        rf"(?:{re.escape(badge_label)}|"
        rf"{re.escape(badge_alt.lower().replace(' ', '_'))}|"
        rf"{re.escape(badge_alt.lower().replace(' ', '__'))}|"
        rf"{re.escape(badge_alt.replace(' ', '%20'))})"
    )
    existing_re = re.compile(
        rf"\[?!\[{re.escape(badge_alt)}\]\("
        rf"https://img\.shields\.io/badge/{label_variants}-[^)]+\)"
        rf"(?:\]\([^)]*\))?",
        re.IGNORECASE,
    )
    if existing_re.search(readme_text):
        updated = existing_re.sub(new_badge, readme_text, count=1)
        return updated, updated != readme_text

    nl = chr(10)
    lines = readme_text.splitlines()
    badge_line_re = re.compile(r"^\s*(\[!\[[^\]]+\]\([^)]+\)\]\([^)]+\)|!\[[^\]]+\]\([^)]+\))\s*$")
    last_badge_idx: int | None = None
    for idx, line in enumerate(lines):
        if badge_line_re.match(line):
            last_badge_idx = idx
        elif last_badge_idx is not None and line.strip():
            break

    if last_badge_idx is not None:
        lines.insert(last_badge_idx + 1, new_badge)
    else:
        insert_at = 0
        if len(lines) >= 2 and set(lines[1].strip()) == {"="} and lines[1].strip():
            insert_at = 2
        elif lines and lines[0].startswith("#"):
            insert_at = 1
        lines.insert(insert_at, new_badge)

    updated = nl.join(lines)
    if readme_text.endswith(nl):
        updated += nl
    return updated, updated != readme_text


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the coverage shield updater.

    Args:
        argv: Optional sequence of CLI arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed ``argparse.Namespace``.
    """
    parser = argparse.ArgumentParser(
        description="Add or update '% doc coverage' and '% test coverage' shields in README.md."
    )
    parser.add_argument(
        "--doc",
        action="store_true",
        help="Add or update the '%% doc coverage' shield in README.md.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Add or update the '%% test coverage' shield in README.md.",
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=Path("README.md"),
        help="Path to README.md (default: README.md).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Root directory of the repository (defaults to parent of scripts/).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the coverage shield updater CLI entry point.

    Args:
        argv: Optional sequence of CLI arguments.

    Returns:
        Exit status code (``0`` on success, ``1`` on error).
    """
    args = parse_args(argv)
    repo_root: Path = (
        args.repo_root.resolve()
        if args.repo_root is not None
        else Path(__file__).resolve().parent.parent
    )
    readme_path: Path = args.readme if args.readme.is_absolute() else (repo_root / args.readme)

    if not readme_path.is_file():
        print(f"Error: README file not found: {readme_path}", file=sys.stderr)
        return 1

    run_doc = args.doc or not (args.doc or args.test)
    run_test = args.test or not (args.doc or args.test)

    content = readme_path.read_text(encoding="utf-8")
    modified = False

    try:
        if run_doc:
            doc_pct = compute_doc_coverage(repo_root)
            content, changed = update_or_insert_shield(
                content, DOC_BADGE_ALT, DOC_BADGE_LABEL, doc_pct
            )
            modified = modified or changed
        if run_test:
            test_pct = compute_test_coverage(repo_root)
            content, changed = update_or_insert_shield(
                content, TEST_BADGE_ALT, TEST_BADGE_LABEL, test_pct
            )
            modified = modified or changed
    except RuntimeError as exc:
        print(f"Error computing coverage: {exc}", file=sys.stderr)
        return 1

    if modified:
        readme_path.write_text(content, encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
