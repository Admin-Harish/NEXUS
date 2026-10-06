"""Turn approved test cases into a runnable pytest or Robot Framework suite on disk."""
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import settings

FRAMEWORKS = ("pytest", "robot")
SUITE_ROOT = Path("/app/generated_testsuites")


def _slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:limit] or "case"


def suite_dir(repo_name: str, framework: str) -> Path:
    stamp = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y%m%d-%H%M%S")
    path = SUITE_ROOT / f"{stamp}_{_slug(repo_name, 30)}_{framework}"
    path.mkdir(parents=True, exist_ok=False)
    return path


# ---------- pytest ----------

PYTEST_HEADER = '''"""NEXUS generated test suite for {repo}.

Source: {source}
Generated: {generated}

Run against a running instance of the service:
    pip install -r requirements.txt
    BASE_URL=http://localhost:{port} pytest -v

Tests run in file order and share the service's state, exactly as NEXUS ran them.
"""
import os

import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:{port}").rstrip("/")
TIMEOUT = 20


def call(record_property, method, path, payload=None):
    response = requests.request(method, BASE_URL + path, json=payload, timeout=TIMEOUT)
    record_property("actual_status", response.status_code)
    record_property("response_excerpt", response.text[:1000])
    return response
'''

PYTEST_CONFTEST = '''"""Streams each test result to NEXUS as it finishes (only when NEXUS_RESULTS is set)."""
import json
import os
import re

RESULTS = os.environ.get("NEXUS_RESULTS")


def pytest_runtest_logreport(report):
    if not RESULTS or (report.when != "call" and report.passed) or (report.when == "teardown" and not report.failed):
        return
    match = re.search(r"test_(\\d+)_", report.nodeid)
    props = dict(report.user_properties)
    line = {
        "index": int(match.group(1)) - 1 if match else None,
        "status": "passed" if report.passed else "skipped" if report.skipped else "failed",
        "duration_ms": int(report.duration * 1000),
        "actual_status": props.get("actual_status"),
        "response_excerpt": props.get("response_excerpt", ""),
        "error": None if report.passed else (report.longreprtext or "")[-1500:],
    }
    with open(RESULTS, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\\n")
'''


def _pytest_case(index: int, case: dict[str, Any]) -> str:
    name = case.get("name") or f"{case['method']} {case['path']}"
    doc = name + (f"\n\n    {case['description']}" if case.get("description") else "")
    doc = '"""' + doc.replace("\\", "\\\\").replace('"""', '\\"\\"\\"') + '\n    """' 
    ref = f"    # Source: {case.get('source', 'discovered')}" + (f", spreadsheet row {case['excel_ref']}" if case.get("excel_ref") else "") + "\n"
    payload = repr(case.get("payload")) if case.get("payload") is not None else "None"
    lines = [
        f"\n\ndef test_{index + 1:02d}_{_slug(name)}(record_property):",
        f"    {doc}",
        ref.rstrip("\n"),
        f"    response = call(record_property, {json.dumps(case['method'].upper())}, {json.dumps(case['path'])}, {payload})",
        f"    assert response.status_code == {int(case.get('expected_status') or 200)}, response.text[:300]",
    ]
    if case.get("expected_contains"):
        lines.append(f"    assert {json.dumps(str(case['expected_contains']))} in response.text, response.text[:300]")
    return "\n".join(lines) + "\n"


def _write_pytest(path: Path, cases: list[dict[str, Any]], meta: dict[str, Any]) -> list[str]:
    (path / "tests").mkdir()
    module = path / "tests" / f"test_{_slug(meta['repo'], 30)}.py"
    module.write_text(PYTEST_HEADER.format(**meta) + "".join(_pytest_case(i, c) for i, c in enumerate(cases)), encoding="utf-8")
    (path / "tests" / "conftest.py").write_text(PYTEST_CONFTEST, encoding="utf-8")
    (path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\naddopts = -p no:cacheprovider\n", encoding="utf-8")
    (path / "requirements.txt").write_text("pytest>=8\nrequests>=2.31\n", encoding="utf-8")
    return [str(module.relative_to(path)), "tests/conftest.py", "pytest.ini", "requirements.txt"]


# ---------- Robot Framework ----------

ROBOT_LISTENER = '''"""Streams each test result to NEXUS as it finishes (only when NEXUS_RESULTS is set)."""
import json
import os
import re

from robot.libraries.BuiltIn import BuiltIn

ROBOT_LISTENER_API_VERSION = 3
RESULTS = os.environ.get("NEXUS_RESULTS")


def end_test(data, result):
    if not RESULTS:
        return
    match = re.match(r"(\\d+)", result.name)
    response = BuiltIn().get_variable_value("${RESP}")
    line = {
        "index": int(match.group(1)) - 1 if match else None,
        "status": {"PASS": "passed", "SKIP": "skipped"}.get(result.status, "failed"),
        "duration_ms": int(result.elapsed_time.total_seconds() * 1000),
        "actual_status": getattr(response, "status_code", None),
        "response_excerpt": getattr(response, "text", "")[:1000],
        "error": None if result.status == "PASS" else result.message[-1500:],
    }
    with open(RESULTS, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\\n")
'''


def _robot_escape(value: Any) -> str:
    """Make arbitrary text safe inside one Robot Framework cell."""
    text = re.sub(r"\s+", " ", str(value)).strip()
    text = text.replace("\\", "\\\\")
    text = re.sub(r"([$@&%])", r"\\\1", text)
    return ("\\" + text) if text.startswith("#") else text


def _robot_case(index: int, case: dict[str, Any]) -> str:
    name = _robot_escape(f"{index + 1:02d} {case.get('name') or case['method'] + ' ' + case['path']}")
    tags = [case.get("source", "discovered")] + ([f"row-{case['excel_ref']}"] if case.get("excel_ref") else [])
    lines = [name]
    if case.get("description"):
        lines.append(f"    [Documentation]    {_robot_escape(case['description'])}")
    lines.append("    [Tags]    " + "    ".join(_robot_escape(t) for t in tags))
    method = case["method"].upper()
    keyword = {"GET": "GET On Session", "POST": "POST On Session", "PUT": "PUT On Session",
               "PATCH": "PATCH On Session", "DELETE": "DELETE On Session"}.get(method, "GET On Session")
    call = f"    ${{RESP}}=    {keyword}    api    {_robot_escape(case['path'])}"
    if case.get("payload") is not None and method in ("POST", "PUT", "PATCH"):
        lines.append(f"    ${{body}}=    Evaluate    json.loads('''{_robot_escape(json.dumps(case['payload']))}''')    json")
        call += "    json=${body}"
    lines.append(call + "    expected_status=any")
    lines.append("    Set Test Variable    ${RESP}")
    lines.append(f"    Should Be Equal As Integers    ${{RESP.status_code}}    {int(case.get('expected_status') or 200)}")
    if case.get("expected_contains"):
        lines.append(f"    Should Contain    ${{RESP.text}}    {_robot_escape(case['expected_contains'])}")
    return "\n".join(lines) + "\n\n"


def _write_robot(path: Path, cases: list[dict[str, Any]], meta: dict[str, Any]) -> list[str]:
    suite = path / f"{_slug(meta['repo'], 30)}.robot"
    header = f"""*** Settings ***
Documentation     NEXUS generated test suite for {_robot_escape(meta['repo'])}.
...               Source: {_robot_escape(meta['source'])}
...               Generated: {meta['generated']}
...               Run: robot --variable BASE_URL:http://localhost:{meta['port']} {suite.name}
Library           RequestsLibrary
Suite Setup       Create Session    api    ${{BASE_URL}}    timeout=20
Suite Teardown    Delete All Sessions

*** Variables ***
${{BASE_URL}}       http://localhost:{meta['port']}

*** Test Cases ***
"""
    suite.write_text(header + "".join(_robot_case(i, c) for i, c in enumerate(cases)), encoding="utf-8")
    (path / "nexus_listener.py").write_text(ROBOT_LISTENER, encoding="utf-8")
    (path / "requirements.txt").write_text("robotframework>=7\nrobotframework-requests>=0.9\n", encoding="utf-8")
    return [suite.name, "nexus_listener.py", "requirements.txt"]


# ---------- shared ----------

README = """# NEXUS generated test suite – {repo}

- Source: {source} @ {commit}
- Framework: {framework_label}
- Generated: {generated}
- Test cases: {count} (approved in NEXUS; definitions in `test_cases.json`)

## Re-run it yourself

Start the service under test (for example `docker build -t target . && docker run -p {port}:{port} target`
in the repository), then:

```bash
pip install -r requirements.txt
{run_command}
```

`results/` holds the output of the run NEXUS performed: {results_note}, the NEXUS HTML report
and the Excel results.
"""


def write_suite(cases: list[dict[str, Any]], framework: str, repo: str, source: str, commit: str, port: int) -> dict[str, Any]:
    if framework not in FRAMEWORKS:
        framework = "pytest"
    path = suite_dir(repo, framework)
    meta = {"repo": repo, "source": source, "port": port,
            "generated": datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M:%S %Z")}
    files = _write_pytest(path, cases, meta) if framework == "pytest" else _write_robot(path, cases, meta)
    (path / "test_cases.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
    run_command = f"BASE_URL=http://localhost:{port} pytest -v" if framework == "pytest" else f"robot --variable BASE_URL:http://localhost:{port} --outputdir results {files[0]}"
    (path / "README.md").write_text(README.format(
        repo=repo, source=source, commit=commit or "-", count=len(cases), port=port, run_command=run_command,
        framework_label="pytest + requests" if framework == "pytest" else "Robot Framework + RequestsLibrary",
        generated=meta["generated"],
        results_note="`junit.xml`" if framework == "pytest" else "`output.xml`, `log.html` and `report.html`",
    ), encoding="utf-8")
    (path / "results").mkdir()
    return {"framework": framework, "dir": str(path), "name": path.name, "main_file": files[0],
            "files": files + ["test_cases.json", "README.md"]}


def command(suite: dict[str, Any], base_url: str) -> list[str]:
    """The command NEXUS runs inside the suite folder."""
    if suite["framework"] == "pytest":
        return ["python", "-m", "pytest", "-v", "--color=no", "--junitxml=results/junit.xml"]
    return ["python", "-m", "robot", "--console", "verbose", "--consolecolors", "off", "--outputdir", "results",
            "--listener", "nexus_listener.py", "--variable", f"BASE_URL:{base_url}", suite["main_file"]]


def zip_suite(suite: dict[str, Any]) -> Path:
    """Zip the suite folder into /app/data/suites so the download does not clutter generated_testsuites."""
    path = Path(suite["dir"])
    target = Path("/app/data/suites")
    target.mkdir(parents=True, exist_ok=True)
    return Path(shutil.make_archive(str(target / path.name), "zip", root_dir=path.parent, base_dir=path.name))


def read_source(suite: dict[str, Any]) -> str:
    return (Path(suite["dir"]) / suite["main_file"]).read_text(encoding="utf-8")
