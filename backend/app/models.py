from typing import Any, Literal
from pydantic import BaseModel, Field


TestMode = Literal["discover", "scenario", "excel"]
Framework = Literal["pytest", "robot"]


class ProjectSpec(BaseModel):
    """A repository to test, plus what the user wants tested in it."""
    id: str
    source_type: Literal["repo"] = "repo"
    mode: TestMode = "discover"
    framework: Framework = "pytest"
    title: str
    summary: str
    detected_stack: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    raw_input: str = ""
    repo_url: str
    scenario: str = ""
    excel_filename: str | None = None
    excel_columns: list[str] = Field(default_factory=list)
    excel_rows: list[dict[str, Any]] = Field(default_factory=list)


class IngestRequest(BaseModel):
    url: str
    scenario: str = ""
    framework: Framework = "pytest"


class PlanRequest(BaseModel):
    project_id: str


class ApprovalRequest(BaseModel):
    approved: bool
    comment: str = ""
    # Test case ids to run. None keeps the current selection.
    selected_ids: list[str] | None = None


class TestCase(BaseModel):
    id: str
    name: str
    description: str
    adapter: Literal["api", "cli", "network", "ui"] = "api"
    priority: Literal["low", "medium", "high"] = "medium"
    method: str = "GET"
    path: str = "/health"
    payload: dict[str, Any] | None = None
    expected_status: int = 200
    expected_contains: str | None = None
    steps: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
    status: str = "draft"
    # discovered | scenario | excel | suggested
    source: str = "discovered"
    excel_ref: str | None = None
    selected: bool = True


class ExecutionPlan(BaseModel):
    id: str
    project_id: str
    objective: str
    environment: dict[str, Any] = Field(default_factory=dict)
    adapters: list[str] = Field(default_factory=list)
    test_cases: list[TestCase] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    status: str = "draft"
    approval_comment: str = ""


class RunRequest(BaseModel):
    plan_id: str
    to_email: str | None = None


class EmailRequest(BaseModel):
    to_email: str | None = None


class RunResult(BaseModel):
    id: str
    plan_id: str
    status: str
    results: list[dict[str, Any]] = Field(default_factory=list)
    analysis: list[dict[str, Any]] = Field(default_factory=list)
    report_url: str | None = None
    started_at: str
    completed_at: str | None = None
    stages: list[dict[str, Any]] = Field(default_factory=list)
    log: list[str] = Field(default_factory=list)
    target: dict[str, Any] = Field(default_factory=dict)
    email: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    total_tests: int = 0
    cases: list[dict[str, Any]] = Field(default_factory=list)
    mode: str = "discover"
    scenario: str = ""
    framework: str = "pytest"
    suite: dict[str, Any] = Field(default_factory=dict)
