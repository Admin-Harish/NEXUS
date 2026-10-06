import asyncio
from datetime import datetime, timezone
from typing import Any
import httpx
from .llm import llm

BODY_METHODS = {"POST", "PUT", "PATCH"}


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


async def execute_plan(plan: dict[str, Any], base_url: str, run: dict[str, Any]) -> None:
    """Run each test case in order against base_url, appending to run["results"] as each one finishes."""
    async with httpx.AsyncClient(base_url=base_url, timeout=20) as client:
        for case in plan.get("test_cases", []):
            if case.get("adapter") != "api":
                run["results"].append({"test_name": case.get("name"), "status": "skipped", "reason": "Adapter is not enabled in this MVP.", "timestamp": now()})
                continue
            method = case.get("method", "GET").upper()
            path = case.get("path", "/health")
            run["current_test"] = case.get("name")
            payload = case.get("payload") if method in BODY_METHODS else None
            started = datetime.now(timezone.utc)
            try:
                response = await client.request(method, path, json=payload)
                body = response.text
                passed = response.status_code == int(case.get("expected_status", 200))
                expected_contains = case.get("expected_contains")
                if expected_contains and str(expected_contains) not in body:
                    passed = False
                result = {
                    "test_name": case.get("name"), "adapter": "api", "method": method,
                    "path": path, "payload": payload, "status": "passed" if passed else "failed",
                    "actual_status": response.status_code, "expected_status": case.get("expected_status", 200),
                    "expected_contains": expected_contains, "response_excerpt": body[:1000],
                    "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000), "timestamp": now(),
                }
                run["results"].append(result)
                if not passed:
                    run["analysis"].append(await asyncio.to_thread(_analysis, case, result))
            except Exception as exc:
                run["results"].append({"test_name": case.get("name"), "adapter": "api", "method": method, "path": path, "status": "failed", "error": str(exc), "timestamp": now()})
                run["analysis"].append({"test_name": case.get("name"), "severity": "high", "root_cause": "The test runner could not reach the deployed service.", "evidence": [str(exc)], "suggested_action": "Check the container logs and service connectivity."})
    run["current_test"] = None
