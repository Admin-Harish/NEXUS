import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4
from .models import ProjectSpec
from .llm import llm
from .excel import structured_cases


def _fallback_plan(project: dict) -> dict:
    """Used when no OpenAI key is configured: only the generic health probe is safe to assume."""
    return {
        "objective": project.get("summary") or "Validate the service end to end.",
        "environment": {"type": "docker", "services": [project.get("title", "target")]},
        "adapters": ["api"],
        "risks": ["No OpenAI key is configured, so only a basic health check was generated."],
        "test_cases": [{
            "name": "Health endpoint returns success",
            "description": "Verify the service health endpoint is available.",
            "adapter": "api", "priority": "high", "method": "GET", "path": "/health",
            "payload": None, "expected_status": 200, "expected_contains": None,
            "steps": ["Start the service", "Call the health endpoint", "Verify the response"],
            "evidence": project.get("evidence", []), "confidence": "medium",
        }],
    }


REPO_URL = re.compile(r"^https://(github\.com|gitlab\.com)/[\w.-]+/[\w.-]+?(\.git)?/?$")
ROUTE_LINE = re.compile(r"@(app|router|bp|blueprint)\.(get|post|put|patch|delete|route)\(|\b(app|router)\.(get|post|put|patch|delete)\(\s*['\"]/", re.IGNORECASE)
SOURCE_EXT = (".py", ".js", ".ts", ".go", ".java", ".rb")


def repo_title(url: str) -> str:
    return re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1]) or "Repository"


def _route_lines(repo: Path, files: list[str], limit: int = 80) -> list[str]:
    lines: list[str] = []
    for rel in files:
        if not rel.endswith(SOURCE_EXT) or "test" in rel.lower() or "node_modules" in rel:
            continue
        for number, line in enumerate((repo / rel).read_text(errors="ignore").splitlines(), 1):
            if ROUTE_LINE.search(line):
                lines.append(f"{rel}:{number}: {line.strip()[:160]}")
                if len(lines) >= limit:
                    return lines
    return lines


def analyze_repo(url: str) -> ProjectSpec:
    url = url.strip()
    if not REPO_URL.match(url):
        raise ValueError("Enter a public GitHub or GitLab clone link, e.g. https://github.com/owner/repo.git")
    temp = Path(tempfile.mkdtemp(prefix="nexus-repo-"))
    evidence: list[str] = []
    stack: list[str] = []
    try:
        completed = subprocess.run(["git", "clone", "--depth", "1", url, str(temp / "repo")], capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            raise ValueError(f"Could not clone {url}. Check that the repository exists and is public.")
        repo = temp / "repo"
        files = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file() and ".git/" not in p.relative_to(repo).as_posix())
        names = set(os.path.basename(x).lower() for x in files)
        if "dockerfile" in names:
            stack.append("Docker"); evidence.append("Dockerfile found: NEXUS can build and deploy this repository")
        else:
            evidence.append("No Dockerfile at the root: deployment will fail")
        specs = [x for x in files if os.path.basename(x).lower() in {"openapi.yaml", "openapi.yml", "openapi.json", "swagger.yaml", "swagger.yml", "swagger.json"}]
        if specs:
            stack.append("OpenAPI"); evidence.append(f"OpenAPI specification: {specs[0]}")
        if any(x.endswith(".py") for x in files): stack.append("Python")
        if any(x.endswith((".js", ".ts")) for x in files): stack.append("JavaScript/TypeScript")
        if any(x.endswith(".go") for x in files): stack.append("Go")
        if any(x.endswith(".java") for x in files): stack.append("Java")
        if "fastapi" in " ".join((repo / x).read_text(errors="ignore") for x in files if x.endswith("requirements.txt")).lower():
            stack.append("FastAPI")
        tests = [x for x in files if "test" in x.lower()]
        if tests: evidence.append(f"{len(tests)} existing test file(s) detected")
        routes = _route_lines(repo, files)
        if routes: evidence.append(f"{len(routes)} HTTP route definition(s) found in source")
        evidence.append(f"Repository contains {len(files)} files")

        readme = next((repo / x for x in files if x.lower() in {"readme.md", "readme.rst", "readme.txt"}), None)
        readme_text = readme.read_text(errors="ignore") if readme else ""
        dockerfile = (repo / "Dockerfile").read_text(errors="ignore") if "dockerfile" in names else ""
        spec_text = (repo / specs[0]).read_text(errors="ignore") if specs else ""
        context = "\n\n".join(part for part in [
            f"Repository: {url}",
            "README:\n" + readme_text[:5000] if readme_text else "",
            "Dockerfile:\n" + dockerfile[:1500] if dockerfile else "",
            "OpenAPI specification:\n" + spec_text[:6000] if spec_text else "",
            "Route definitions:\n" + "\n".join(routes) if routes else "",
            "Files:\n" + "\n".join(files[:150]),
        ] if part)
        summary = next((line.strip("# ").strip() for line in readme_text.splitlines() if line.strip() and not line.startswith("#")), "") or f"Repository analysis for {url}"
        return ProjectSpec(id=str(uuid4()), source_type="repo", title=repo_title(url), summary=summary[:500],
                           detected_stack=sorted(set(stack)) or ["Unknown"], evidence=evidence,
                           raw_input=context, repo_url=url)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def _unwrap(generated) -> dict:
    """The model sometimes wraps its answer in a single key, e.g. {"execution_plan": {...}}."""
    if isinstance(generated, dict) and "test_cases" not in generated:
        generated = next((v for v in generated.values() if isinstance(v, dict) and "test_cases" in v), generated)
    return generated if isinstance(generated, dict) else {}


def _clean_cases(cases: list, source: str, selected: bool) -> list[dict]:
    cleaned = []
    for case in cases or []:
        if not isinstance(case, dict) or not case.get("path"):
            continue
        case = {k: v for k, v in case.items() if k != "id"}
        case.setdefault("name", f"{case.get('method', 'GET')} {case['path']}")
        case.setdefault("description", "")
        case["source"], case["selected"] = source, selected
        # Error and empty responses rarely echo documented wording, so judge them on status alone.
        if int(case.get("expected_status") or 200) >= 400 or case.get("expected_status") == 204:
            case["expected_contains"] = None
        if case.get("excel_ref") is not None:
            case["excel_ref"] = str(case["excel_ref"])
        cleaned.append(case)
    return cleaned


def _context(project: ProjectSpec) -> dict:
    """What the model sees about the repository."""
    return {"title": project.title, "summary": project.summary, "detected_stack": project.detected_stack,
            "evidence": project.evidence, "repository": project.raw_input}


def generate_plan(project: ProjectSpec) -> dict:
    context = _context(project)
    if project.mode == "excel":
        excel_cases = structured_cases(project.excel_rows)
        if excel_cases is None:
            excel_cases = _unwrap(llm.convert_excel(project.excel_rows, context)).get("test_cases", [])
        excel_cases = _clean_cases(excel_cases, "excel", True)
        suggested = _clean_cases(_unwrap(llm.suggest_tests(context, excel_cases)).get("test_cases", []), "suggested", False)
        if not excel_cases:
            raise ValueError("None of the spreadsheet rows could be turned into a test case.")
        return {
            "objective": f"Run the {len(excel_cases)} test case(s) from {project.excel_filename or 'the spreadsheet'} against {project.title}.",
            "environment": {"type": "docker", "services": [project.title]},
            "adapters": ["api"],
            "risks": ["Suggested additions are not selected by default: tick the ones to run and they are added to the exported sheet."] if suggested else [],
            "test_cases": excel_cases + suggested,
        }
    generated = _unwrap(llm.generate_plan(context, project.scenario if project.mode == "scenario" else ""))
    cases = _clean_cases(generated.get("test_cases"), "scenario" if project.mode == "scenario" else "discovered", True)
    if not cases:
        fallback = _fallback_plan(project.model_dump())
        return fallback | {"test_cases": _clean_cases(fallback["test_cases"], "discovered", True)}
    return generated | {"test_cases": cases}
