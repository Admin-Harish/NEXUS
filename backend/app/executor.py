"""Runs a generated pytest / Robot Framework suite and streams its results into the run."""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import generator
from .llm import llm


def now():
    return datetime.now(timezone.utc).isoformat()


def _analysis(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    fallback = {
        "test_name": case.get("name"), "severity": "medium",
        "root_cause": "Observed response did not match the expected status or body.",
        "evidence": [f"Expected HTTP {result.get('expected_status')}", f"Observed HTTP {result.get('actual_status')}"],
        "suggested_action": "Inspect the service logs and endpoint contract.",
    }
    try:
        ai = llm.analyze_failure(result)
    except Exception:
        ai = None
    # The model sometimes wraps its answer in a single key.
    if isinstance(ai, dict) and "root_cause" not in ai and len(ai) == 1 and isinstance(next(iter(ai.values())), dict):
        ai = next(iter(ai.values()))
    if not isinstance(ai, dict) or "root_cause" not in ai:
        return fallback
    return {**fallback, **ai, "test_name": case.get("name")}


def _result(case: dict[str, Any], line: dict[str, Any]) -> dict[str, Any]:
    return {
        "test_name": case.get("name"), "adapter": "api", "method": case.get("method", "GET").upper(),
        "path": case.get("path"), "payload": case.get("payload"), "status": line.get("status", "failed"),
        "actual_status": line.get("actual_status"), "expected_status": case.get("expected_status", 200),
        "expected_contains": case.get("expected_contains"), "response_excerpt": line.get("response_excerpt") or "",
        "duration_ms": line.get("duration_ms"), "error": line.get("error"), "timestamp": now(),
    }


async def run_suite(suite: dict[str, Any], cases: list[dict[str, Any]], base_url: str, run: dict[str, Any], log: Callable[[str], None]) -> int:
    """Execute the suite against base_url. Results are appended to run["results"] as each test finishes."""
    folder = Path(suite["dir"])
    results_file = folder / "results" / "nexus_results.jsonl"
    results_file.unlink(missing_ok=True)
    cmd = generator.command(suite, base_url)
    log("$ " + " ".join(cmd))
    process = await asyncio.create_subprocess_exec(
        *cmd, cwd=folder, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env=os.environ | {"BASE_URL": base_url, "NEXUS_RESULTS": str(results_file), "PYTHONUNBUFFERED": "1"},
    )
    consumed = 0
    pending_analysis: list[asyncio.Task] = []

    def drain() -> None:
        nonlocal consumed
        if not results_file.exists():
            return
        lines = results_file.read_text(encoding="utf-8").splitlines()
        for raw in lines[consumed:]:
            line = json.loads(raw)
            index = line.get("index")
            case = cases[index] if isinstance(index, int) and 0 <= index < len(cases) else {"name": f"test {index}"}
            result = _result(case, line)
            run["results"].append(result)
            following = len(run["results"])
            run["current_test"] = cases[following]["name"] if following < len(cases) else None
            if result["status"] == "failed":
                pending_analysis.append(asyncio.create_task(asyncio.to_thread(_analysis, case, result)))
        consumed = len(lines)

    run["current_test"] = cases[0]["name"] if cases else None

    async def pump_output():
        assert process.stdout
        async for raw in process.stdout:
            text = raw.decode(errors="ignore").rstrip()
            if text and not set(text) <= set("=-. "):
                log(text[:240])

    reader = asyncio.create_task(pump_output())
    while process.returncode is None:
        drain()
        try:
            await asyncio.wait_for(process.wait(), timeout=0.5)
        except asyncio.TimeoutError:
            pass
    await reader
    drain()
    run["current_test"] = None
    for task in pending_analysis:
        run["analysis"].append(await task)
    log(f"{suite['framework']} exited with code {process.returncode}")
    return process.returncode
