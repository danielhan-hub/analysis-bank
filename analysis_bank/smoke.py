"""Smoke-test a procedure.sql against the live Snowflake account.

Used by:
- ``ads_ms_analysis.promote_code()`` after the broker writes a candidate
- ``analysis_bank.AnalysisBankReceiver.evaluate()`` as a pre-LLM gate

The test runs ``snow sql -f <procedure.sql>`` to compile the procedure,
then extracts and runs the documented SAMPLE CALL to confirm it works.
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class SmokeTestError(RuntimeError):
    """Raised when the smoke test fails."""


def smoke_test_procedure(proc_sql: Path, *, verbose: bool = True) -> None:
    """CREATE the procedure then run its embedded SAMPLE CALL.

    Args:
        proc_sql: Path to the procedure.sql file.
        verbose: If True, print a result preview on success.

    Raises:
        SmokeTestError: If the procedure fails to create or the SAMPLE CALL fails.
    """
    logger.info(f"Smoke-testing procedure: {proc_sql}")

    result = subprocess.run(
        ["snow", "sql", "-f", str(proc_sql)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise SmokeTestError(
            f"Procedure creation failed.\n"
            f"stderr: {result.stderr.strip()}\n"
            f"stdout: {result.stdout.strip()}"
        )
    logger.info("Procedure created successfully.")

    content = proc_sql.read_text(encoding="utf-8")
    call_sql = parse_sample_call(content)
    if call_sql is None:
        logger.warning("No SAMPLE CALL found in procedure.sql — skipping call test.")
        return

    use_block = extract_use_statements(content)
    test_sql = f"{use_block}\n{call_sql}" if use_block else call_sql

    with tempfile.NamedTemporaryFile(suffix=".sql", mode="w", delete=False, encoding="utf-8") as tmp:
        tmp.write(test_sql)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ["snow", "sql", "-f", tmp_path],
            capture_output=True,
            text=True,
            timeout=300,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if result.returncode != 0:
        raise SmokeTestError(
            f"SAMPLE CALL failed.\n"
            f"Call SQL:\n{call_sql}\n\n"
            f"stderr: {result.stderr.strip()}\n"
            f"stdout: {result.stdout.strip()}"
        )

    logger.info("SAMPLE CALL succeeded.")
    if verbose:
        preview = result.stdout.strip()[:500]
        print(f"\nSmoke test passed. Result preview:\n{preview}")


def parse_sample_call(proc_sql_text: str) -> str | None:
    """Extract the SAMPLE CALL SQL from the procedure header comment block.

    Handles three formats found in the analysis bank:
      1. Same-line:   -- SAMPLE CALL: CALL foo(args);
      2. No-SAMPLE:   -- CALL foo(args)
      3. Multi-line:  -- SAMPLE CALL:\\n--   CALL foo(\\n--       arg,\\n--   );
    """
    lines = proc_sql_text.splitlines()
    call_lines: list[str] = []
    state = "searching"

    for line in lines:
        stripped = line.strip()

        if state == "searching":
            if not stripped.startswith("--"):
                if re.match(r"CREATE\b", stripped, re.IGNORECASE):
                    break
                continue

            code = re.sub(r"^--\s*", "", stripped).strip()

            if re.search(r"SAMPLE\s+CALL", code, re.IGNORECASE):
                m = re.search(r"\bCALL\s+\w", code, re.IGNORECASE)
                if m:
                    call_lines.append(code[m.start():])
                state = "collecting"

            elif re.match(r"CALL\s+\w", code, re.IGNORECASE):
                call_lines.append(code)
                state = "collecting"

        else:  # collecting
            if not stripped.startswith("--"):
                break
            code = re.sub(r"^--\s*", "", stripped).strip()
            if not code:
                break
            call_lines.append(code)

    return "\n".join(call_lines) or None


def extract_use_statements(proc_sql_text: str) -> str:
    """Return all USE ROLE/SCHEMA/WAREHOUSE lines from the procedure header."""
    use_lines = []
    for line in proc_sql_text.splitlines():
        s = line.strip()
        if re.match(r"USE\s+", s, re.IGNORECASE):
            use_lines.append(s if s.endswith(";") else s + ";")
    return "\n".join(use_lines)
