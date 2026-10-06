from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from .config import settings
from .models import *
from .store import projects, plans, runs
from .analyzer import analyze_repo, generate_plan
from .excel import export_results, read_rows
from .report import REPORT_DIR
from .emailer import send_report
from .pipeline import new_run, run_pipeline
from . import deployer
import asyncio

app = FastAPI(title="NEXUS", version="0.3.0", description="Adapter-based E2E test planning, deployment and execution")
background_tasks: set[asyncio.Task] = set()


@app.on_event("startup")
async def remove_leftover_targets():
    await asyncio.to_thread(deployer.cleanup_leftovers)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health():
    return {"status": "ok", "service": "nexus-backend"}


@app.post("/api/projects/ingest", response_model=ProjectSpec)
def ingest(request: IngestRequest):
    """Analyze a repository. With a scenario, the plan targets that scenario; without, NEXUS discovers tests."""
    try:
        project = analyze_repo(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if request.scenario.strip():
        project.mode, project.scenario = "scenario", request.scenario.strip()
    projects[project.id] = project.model_dump()
    return project


@app.post("/api/projects/ingest-excel", response_model=ProjectSpec)
async def ingest_excel(url: str = Form(...), file: UploadFile = File(...)):
    """Analyze a repository and attach the user's spreadsheet of test cases to run against it."""
    try:
        columns, rows = read_rows(file.filename or "", await file.read())
        project = await asyncio.to_thread(analyze_repo, url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    project.mode, project.excel_filename, project.excel_columns, project.excel_rows = "excel", file.filename, columns, rows
    project.evidence.append(f"{len(rows)} test case row(s) read from {file.filename}")
    projects[project.id] = project.model_dump()
    return project


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    if project_id not in projects: raise HTTPException(404, "Project not found")
    return projects[project_id]


@app.post("/api/plans", response_model=ExecutionPlan)
def create_plan(request: PlanRequest):
    project = projects.get(request.project_id)
    if not project: raise HTTPException(404, "Project not found")
    try:
        generated = generate_plan(ProjectSpec(**project))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    test_cases = []
    for item in generated.get("test_cases", []):
        test_cases.append(TestCase(id=str(uuid4()), **{k: v for k, v in item.items() if k != "id"}))
    plan = ExecutionPlan(id=str(uuid4()), project_id=request.project_id, objective=generated.get("objective", "Validate project behavior"), environment=generated.get("environment", {}), adapters=generated.get("adapters", ["api"]), test_cases=test_cases, risks=generated.get("risks", []))
    plans[plan.id] = plan.model_dump()
    return plan


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str):
    if plan_id not in plans: raise HTTPException(404, "Plan not found")
    return plans[plan_id]


@app.post("/api/plans/{plan_id}/approve")
def approve_plan(plan_id: str, request: ApprovalRequest):
    plan = plans.get(plan_id)
    if not plan: raise HTTPException(404, "Plan not found")
    if request.selected_ids is not None:
        chosen = set(request.selected_ids)
        for case in plan["test_cases"]:
            case["selected"] = case["id"] in chosen
    if request.approved and not any(c["selected"] for c in plan["test_cases"]):
        raise HTTPException(400, "Select at least one test case to run")
    plan["status"] = "approved" if request.approved else "rejected"
    plan["approval_comment"] = request.comment
    return plan


@app.post("/api/runs", response_model=RunResult)
async def run_plan(request: RunRequest):
    """Deploy the target as a container and execute the approved plan against it in the background.
    Poll GET /api/runs/{id} for live progress."""
    plan = plans.get(request.plan_id)
    if not plan: raise HTTPException(404, "Plan not found")
    if plan.get("status") != "approved": raise HTTPException(400, "Approve the execution plan first")
    project = projects.get(plan["project_id"], {})
    # Only the test cases the user ticked are run.
    plan = plan | {"test_cases": [c for c in plan["test_cases"] if c.get("selected", True)]}
    run = new_run(str(uuid4()), plan, project)
    runs[run["id"]] = run
    task = asyncio.create_task(run_pipeline(run, plan, project, request.to_email))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return run


@app.get("/api/runs/{run_id}", response_model=RunResult)
def get_run(run_id: str):
    if run_id not in runs: raise HTTPException(404, "Run not found")
    return runs[run_id]


@app.post("/api/runs/{run_id}/email")
def email_run(run_id: str, request: EmailRequest):
    run = runs.get(run_id)
    if not run: raise HTTPException(404, "Run not found")
    workbook = export_results(run.get("cases", []), run["results"], run["target"].get("name", "run"))
    sent, message = send_report(run, request.to_email, REPORT_DIR / f"{run_id}.html", workbook)
    run["email"] = {"sent": sent, "message": message, "to": request.to_email or settings.report_to_email}
    return {"sent": sent, "message": message}


@app.get("/api/runs/{run_id}/excel")
def download_excel(run_id: str):
    """The executed test cases as a spreadsheet, with results and the cases NEXUS added marked."""
    run = runs.get(run_id)
    if not run: raise HTTPException(404, "Run not found")
    name = run["target"].get("name", "run")
    data = export_results(run["cases"], run["results"], name)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="nexus-testcases-{name}-{run_id[:8]}.xlsx"'})


@app.get("/api/runs/{run_id}/report")
def download_report(run_id: str):
    path = REPORT_DIR / f"{run_id}.html"
    if not path.exists(): raise HTTPException(404, "Report not found")
    name = runs.get(run_id, {}).get("target", {}).get("name", "run")
    return FileResponse(path, media_type="text/html", filename=f"nexus-report-{name}-{run_id[:8]}.html")


@app.get("/reports/{filename}")
def report_file(filename: str):
    path = REPORT_DIR / filename
    if not path.exists() or path.suffix != ".html": raise HTTPException(404, "Report not found")
    return FileResponse(path)
