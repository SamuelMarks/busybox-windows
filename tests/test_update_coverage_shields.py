"""Unit tests for ``scripts.update_coverage_shields``."""

from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.update_coverage_shields import (
    DOC_BADGE_ALT,
    DOC_BADGE_LABEL,
    TEST_BADGE_ALT,
    TEST_BADGE_LABEL,
    build_shield_markdown,
    compute_doc_coverage,
    compute_test_coverage,
    format_percentage,
    get_shield_color,
    main,
    parse_args,
    update_or_insert_shield,
)


def test_get_shield_color_and_format_percentage() -> None:
    """Verify color thresholds and percentage formatting branches."""
    assert get_shield_color(100.0) == "brightgreen"
    assert get_shield_color(95.0) == "brightgreen"
    assert get_shield_color(92.5) == "green"
    assert get_shield_color(85.0) == "yellowgreen"
    assert get_shield_color(75.0) == "yellow"
    assert get_shield_color(65.0) == "orange"
    assert get_shield_color(50.0) == "red"

    assert format_percentage(100.0) == "100"
    assert format_percentage(99.99) == "100"
    assert format_percentage(98.5) == "98.5"
    assert format_percentage(0.0) == "0"

    assert (
        build_shield_markdown(DOC_BADGE_ALT, DOC_BADGE_LABEL, 100.0)
        == "![Doc Coverage](https://img.shields.io/badge/doc%20coverage-100%25-brightgreen)"
    )


def test_update_or_insert_shield_branches() -> None:
    """Verify shield insertion and update across Markdown heading styles."""
    nl = chr(10)
    # 1. Existing badges block: inserts after the last badge
    readme_with_badges = nl.join(
        [
            "Title",
            "=====",
            "",
            "[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://example.com)",
            "[![CI](https://example.com/badge.svg)](https://example.com)",
            "",
            "Body paragraph.",
            "",
        ]
    )
    updated1, changed1 = update_or_insert_shield(
        readme_with_badges, DOC_BADGE_ALT, DOC_BADGE_LABEL, 100.0
    )
    assert changed1 is True
    assert "![Doc Coverage](https://img.shields.io/badge/doc%20coverage-100%25-brightgreen)" in (
        updated1
    )
    assert updated1.endswith(nl)

    # 2. Updating an already up-to-date badge returns changed=False
    updated2, changed2 = update_or_insert_shield(updated1, DOC_BADGE_ALT, DOC_BADGE_LABEL, 100.0)
    assert changed2 is False
    assert updated2 == updated1

    # 3. Updating an existing badge with a new percentage returns changed=True
    updated3, changed3 = update_or_insert_shield(updated1, DOC_BADGE_ALT, DOC_BADGE_LABEL, 85.4)
    assert changed3 is True
    assert "85.4%25-yellowgreen" in updated3

    # 4. Setext heading without existing badges and without trailing newline
    setext_readme = f"Project{nl}======={nl}Hello"
    updated4, changed4 = update_or_insert_shield(
        setext_readme, TEST_BADGE_ALT, TEST_BADGE_LABEL, 100.0
    )
    assert changed4 is True
    assert not updated4.endswith(nl)
    assert updated4.splitlines()[2].startswith("![Test Coverage]")

    # 5. ATX (#) heading without existing badges
    atx_readme = f"# Project{nl}Hello{nl}"
    updated5, changed5 = update_or_insert_shield(
        atx_readme, TEST_BADGE_ALT, TEST_BADGE_LABEL, 100.0
    )
    assert changed5 is True
    assert updated5.splitlines()[1].startswith("![Test Coverage]")

    # 6. Plain text without headings or badges
    plain_readme = f"Plain text only{nl}"
    updated6, changed6 = update_or_insert_shield(
        plain_readme, DOC_BADGE_ALT, DOC_BADGE_LABEL, 100.0
    )
    assert changed6 is True
    assert updated6.splitlines()[0].startswith("![Doc Coverage]")


def test_compute_doc_and_test_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify subprocess parsing and fallback branches for doc and test coverage.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    nl = chr(10)

    # Doc coverage success
    def fake_interrogate_ok(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        """Return a simulated successful interrogate CompletedProcess.

        Returns:
            Simulated CompletedProcess with 100% actual coverage.
        """
        return subprocess.CompletedProcess(
            args=["interrogate"],
            returncode=0,
            stdout=f"RESULT: PASSED (minimum: 100.0%, actual: 100.0%){nl}",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_interrogate_ok)
    assert compute_doc_coverage(tmp_path) == 100.0

    # Doc coverage parse error
    def fake_interrogate_bad(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        """Return a simulated malformed interrogate CompletedProcess.

        Returns:
            Simulated CompletedProcess with unparseable output.
        """
        return subprocess.CompletedProcess(
            args=["interrogate"],
            returncode=1,
            stdout="unexpected output",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_interrogate_bad)
    with pytest.raises(RuntimeError, match="Unable to parse interrogate output"):
        compute_doc_coverage(tmp_path)

    # Test coverage from existing .coverage file via `coverage report`
    cov_file = tmp_path / ".coverage"
    cov_file.write_text("dummy", encoding="utf-8")

    def fake_cov_report_ok(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        """Return a simulated successful coverage report CompletedProcess.

        Returns:
            Simulated CompletedProcess with TOTAL 100%.
        """
        return subprocess.CompletedProcess(
            args=["coverage", "report"],
            returncode=0,
            stdout=f"Name Stmts Miss Cover{nl}TOTAL 100 0 100%{nl}",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_cov_report_ok)
    assert compute_test_coverage(tmp_path) == 100.0

    # Fallback when .coverage exists and coverage report returns 0 without TOTAL
    calls: list[list[str]] = []

    def fake_cov_then_pytest(
        cmd: list[str], *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Simulate coverage report without TOTAL line followed by pytest output.

        Args:
            cmd: Subprocess command list.

        Returns:
            Simulated CompletedProcess for coverage or pytest.
        """
        calls.append(cmd)
        if "coverage" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="No total line here", stderr=""
            )
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"TOTAL 200 5 97.5%{nl}",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_cov_then_pytest)
    assert compute_test_coverage(tmp_path) == 97.5
    assert len(calls) == 2

    # Fallback when .coverage exists and coverage report fails (returncode != 0)
    def fake_cov_fail_then_pytest(
        cmd: list[str], *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Simulate failing coverage report followed by successful pytest run.

        Args:
            cmd: Subprocess command list.

        Returns:
            Simulated CompletedProcess for coverage failure or pytest success.
        """
        if "coverage" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="err")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"TOTAL 50 0 100%{nl}",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_cov_fail_then_pytest)
    assert compute_test_coverage(tmp_path) == 100.0

    # Test coverage when .coverage does not exist and pytest output cannot be parsed
    cov_file.unlink()

    def fake_pytest_bad(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        """Simulate malformed pytest output.

        Returns:
            Simulated CompletedProcess lacking a TOTAL line.
        """
        return subprocess.CompletedProcess(
            args=["pytest"], returncode=1, stdout="no coverage table", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_pytest_bad)
    with pytest.raises(RuntimeError, match="Unable to parse coverage output"):
        compute_test_coverage(tmp_path)


def test_cli_main_and_entry_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify CLI argument parsing, main execution paths, and ``__main__`` invocation.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    nl = chr(10)
    parsed = parse_args(["--doc", "--readme", "README.md"])
    assert parsed.doc is True
    assert parsed.test is False
    assert parsed.readme == Path("README.md")

    # Missing README file returns 1
    rc_missing = main(["--repo-root", str(tmp_path), "--readme", "MISSING.md"])
    assert rc_missing == 1
    assert "README file not found" in capsys.readouterr().err

    readme = tmp_path / "README.md"
    readme.write_text(
        nl.join(
            [
                "BusyBox Windows",
                "===============",
                "",
                "[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://example.com)",
                "",
                "Text.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("scripts.update_coverage_shields.compute_doc_coverage", lambda _r: 100.0)
    monkeypatch.setattr("scripts.update_coverage_shields.compute_test_coverage", lambda _r: 100.0)

    # Update both badges (default when neither --doc nor --test is passed)
    rc_both = main(["--repo-root", str(tmp_path), "--readme", str(readme)])
    assert rc_both == 0
    text_after = readme.read_text(encoding="utf-8")
    assert "![Doc Coverage]" in text_after
    assert "![Test Coverage]" in text_after

    # Second run when already up-to-date does not rewrite file
    rc_noop = main(["--repo-root", str(tmp_path)])
    assert rc_noop == 0

    # Run with --doc only (run_test is False) and --test only (run_doc is False)
    assert main(["--repo-root", str(tmp_path), "--doc"]) == 0
    assert main(["--repo-root", str(tmp_path), "--test"]) == 0

    # RuntimeError during coverage computation returns 1
    def raise_err(_r: Path) -> float:
        """Raise a simulated RuntimeError during coverage calculation.

        Raises:
            RuntimeError: Always raised to test error handling.
        """
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("scripts.update_coverage_shields.compute_doc_coverage", raise_err)
    rc_err = main(["--repo-root", str(tmp_path), "--doc"])
    assert rc_err == 1
    assert "Error computing coverage: simulated failure" in capsys.readouterr().err

    # __main__ execution with missing readme exits with code 1
    old_argv = sys.argv
    sys.argv = [
        "update_coverage_shields.py",
        "--repo-root",
        str(tmp_path),
        "--readme",
        "NONEXISTENT.md",
    ]
    try:
        script_path = (
            Path(__file__).resolve().parent.parent / "scripts" / "update_coverage_shields.py"
        )
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(str(script_path), run_name="__main__")
        assert exc_info.value.code == 1
    finally:
        sys.argv = old_argv
    capsys.readouterr()
