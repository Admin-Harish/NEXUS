import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import deployer
from .config import settings
from .emailer import send_report
from .excel import export_results
from . import generator
from .executor import run_suite
from .report import REPORT_DIR, render_report

STAGES = [
    ("prepare", "Prepare source"),
    ("generate", "Generate test suite"),
    ("build", "Build container image"),
    ("start", "Start container"),
    ("test", "Execute test cases"),
    ("cleanup", "Remove containers"),
    ("report", "Generate report & package suite"),
    ("email", "Email report"),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run(run_id: str, plan: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    source = project.get("repo_url", "")
    return {
        "id": run_id, "plan_id": plan["id"], "status": "queued", "results": [], "analysis": [],
        "started_at": utc_now(), "completed_at": None, "report_url": None, "error": None,
        "total_tests": len(plan.get("test_cases", [])), "current_test": None,
        "cases": plan.get("test_cases", []), "mode": project.get("mode", "discover"), "scenario": project.get("scenario", ""),
        "stages": [{"key": key, "label": label, "status": "pending", "detail": ""} for key, label in STAGES],
        "log": [], "email": {}, "suite": {}, "framework": project.get("framework", "pytest"),
        "target": {"name": project.get("title", "target"), "source": source, "commit": "", "image": "", "container": "", "url": ""},
    }


class _Stage:
    def __init__(self, run: dict[str, Any], key: str):
        self.stage = next(s for s in run["stages"] if s["key"] == key)
        self.run = run

    def __enter__(self):
        self.stage["status"] = "running"
        self.stage["started_at"] = utc_now()
        self.run["status"] = self.stage["key"]
        return self.stage

    def __exit__(self, exc_type, exc, tb):
        self.stage["status"] = "failed" if exc_type else ("done" if self.stage["status"] == "running" else self.stage["status"])
        if exc_type:
            self.stage["detail"] = str(exc)[:500]
        return False


async def run_pipeline(run: dict[str, Any], plan: dict[str, Any], project: dict[str, Any], to_email: str | None) -> None:
    def log(line: str) -> None:
        run["log"].append(f"{datetime.now().strftime('%H:%M:%S')}  {line}")
        del run["log"][:-400]

    short = run["id"][:8]
    tag = f"nexus-target-{short}:latest"
    name = f"nexus-target-{short}"
    source_dir: Path | None = None
    started_container = False
    built_image = False
    try:
        with _Stage(run, "prepare") as stage:
            source_dir, sha = await asyncio.to_thread(deployer.clone, project["repo_url"], log)
            run["target"]["commit"] = sha
            stage["detail"] = f"Cloned {project['repo_url']} @ {sha}"
            port = deployer.exposed_port(source_dir) if (source_dir / "Dockerfile").is_file() else 8000

        with _Stage(run, "generate") as stage:
            run["suite"] = await asyncio.to_thread(
                generator.write_suite, run["cases"], run["framework"], run["target"]["name"],
                project["repo_url"], run["target"]["commit"], port)
            run["suite"]["source"] = generator.read_source(run["suite"])
            stage["detail"] = f"{run['suite']['host_dir']}/{run['suite']['main_file']}"
            log(f"Wrote {run['framework']} suite with {len(run['cases'])} test(s) to {stage['detail']}")

        with _Stage(run, "build") as stage:
            built_image = True
            await asyncio.to_thread(deployer.build, source_dir, tag, run["id"], log)
            run["target"]["image"] = tag
            stage["detail"] = tag

        with _Stage(run, "start") as stage:
            started_container = True
            await asyncio.to_thread(deployer.start, tag, name, run["id"], log)
            base_url = await asyncio.to_thread(deployer.wait_ready, name, port, log)
            run["target"].update(container=name, url=base_url)
            stage["detail"] = f"{name} ready at {base_url}"

        with _Stage(run, "test") as stage:
            log(f"Running {run['total_tests']} test case(s) against {base_url}")
            await run_suite(run["suite"], run["cases"], base_url, run, log)
            passed = sum(1 for r in run["results"] if r.get("status") == "passed")
            stage["detail"] = f"{passed}/{len(run['results'])} passed"
            log(stage["detail"])
            run["target"]["service_log"] = await asyncio.to_thread(deployer.container_logs, name)
    except Exception as exc:
        run["error"] = str(exc)
        log(f"ERROR: {exc}")
        for stage in run["stages"]:
            if stage["status"] == "pending" and stage["key"] in ("prepare", "generate", "build", "start", "test"):
                stage["status"] = "skipped"

    results = run["results"]
    if run["error"]:
        run["outcome"] = "error"
    elif not results:
        run["outcome"] = "error"
        run["error"] = "No test cases were executed."
    else:
        run["outcome"] = "failed" if any(r.get("status") == "failed" for r in results) else "passed"

    with _Stage(run, "cleanup") as stage:
        try:
            await asyncio.to_thread(deployer.teardown, name if started_container else None, tag if built_image else None, source_dir, log)
            stage["detail"] = "Target container and image removed"
        except Exception as exc:
            stage["detail"] = f"Cleanup problem: {exc}"
            log(stage["detail"])

    run["completed_at"] = utc_now()
    workbook = export_results(run["cases"], run["results"], run["target"]["name"])
    suite_zip: Path | None = None
    with _Stage(run, "report") as stage:
        run["report_url"] = render_report(run | {"status": run["outcome"]})
        stage["detail"] = "HTML report ready"
        if run["suite"].get("dir"):
            # Keep the evidence next to the scripts, then zip the folder for download and email.
            results_dir = Path(run["suite"]["dir"]) / "results"
            (results_dir / "nexus_report.html").write_bytes((REPORT_DIR / f"{run['id']}.html").read_bytes())
            (results_dir / "nexus_results.xlsx").write_bytes(workbook)
            suite_zip = await asyncio.to_thread(generator.zip_suite, run["suite"])
            run["suite"]["zip"] = str(suite_zip)
            stage["detail"] = f"HTML report and suite archive {suite_zip.name} ready"

    with _Stage(run, "email") as stage:
        sent, message = await asyncio.to_thread(send_report, run | {"status": run["outcome"]}, to_email, REPORT_DIR / f"{run['id']}.html", workbook, suite_zip)
        run["email"] = {"sent": sent, "message": message, "to": to_email or settings.report_to_email}
        stage["detail"] = message
        if not sent:
            stage["status"] = "failed"
        log(message)

    run["status"] = run["outcome"]
