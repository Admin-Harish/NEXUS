import json
import re
from typing import Any
from openai import OpenAI
from .config import settings

CASE_SCHEMA = {
    "name": "string", "description": "string", "adapter": "api",
    "priority": "high|medium|low", "method": "GET|POST|PUT|PATCH|DELETE",
    "path": "string", "payload": {}, "expected_status": 200,
    "expected_contains": "string|null", "steps": ["string"],
    "evidence": ["string"], "confidence": "high|medium|low",
}

RULES = """You are NEXUS, a safe E2E test planning assistant. Return JSON only, with the keys of the schema at the top level. Never invent evidence. Use only the api adapter. Do not generate shell commands.
The tests are executed for real, in the listed order, as single HTTP requests against a freshly started instance of the service with empty state.
Rules:
- Use only endpoints that appear in the supplied README, OpenAPI specification or route definitions. Never guess paths.
- Paths must be concrete (no {placeholders}). When a test needs an existing resource, create it in an earlier test and use the id the documentation says it will receive.
- Use the exact status codes the documentation or code defines, including for negative tests (validation errors, not found, conflicts).
- Set expected_contains only to a short string that is certain to appear in the response body (for example a name sent in the payload); otherwise null. Always null for 4xx and 204 responses.
- payload is a JSON object for POST/PUT/PATCH and null for GET/DELETE.
- Order matters: state carries over between tests. Put every test that relies on a resource (reads, updates, duplicate/conflict checks) before the test that deletes it."""


class LLMService:
    def __init__(self):
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    def _json(self, system: str, user: str) -> dict[str, Any] | list[Any] | None:
        if not self.client:
            return None
        response = self.client.chat.completions.create(
            model=settings.openai_model,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        content = response.choices[0].message.content or "{}"
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
        return json.loads(content)

    def generate_plan(self, project: dict[str, Any], scenario: str = "") -> dict[str, Any] | None:
        """Discover test cases for the repository, or for one user scenario when given."""
        if scenario:
            task = ("Create an execution plan and test cases for the user's scenario below, against this repository. "
                    "Cover the scenario's happy path and its relevant error cases, adding only the setup requests the scenario needs. "
                    "If part of the scenario is not supported by the documented API, say so in risks and leave it out.\n"
                    f"Scenario: {scenario}")
        else:
            task = "Create an execution plan and 6 to 10 test cases covering health, the main happy paths and the documented error cases."
        user = json.dumps({
            "task": task,
            "project": project,
            "schema": {"objective": "string", "environment": {"type": "docker", "services": ["string"]},
                       "adapters": ["api"], "risks": ["string"], "test_cases": [CASE_SCHEMA]},
        })
        return self._json(RULES, user)

    def convert_excel(self, rows: list[dict[str, Any]], project: dict[str, Any]) -> dict[str, Any] | None:
        """Turn free-form spreadsheet rows into executable test cases, one per row."""
        user = json.dumps({
            "task": ("Convert each spreadsheet row into exactly one executable test case against this repository, in row order. "
                     "Do not add, merge or drop rows. Copy the row's ID (or row number) into excel_ref and use the row's wording for name. "
                     "If a row cannot be mapped to a documented endpoint, still return it with confidence low and explain in description."),
            "rows": rows,
            "project": project,
            "schema": {"test_cases": [CASE_SCHEMA | {"excel_ref": "string"}]},
        })
        return self._json(RULES, user)

    def suggest_tests(self, project: dict[str, Any], existing: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Propose test cases that the user's existing list does not cover yet."""
        user = json.dumps({
            "task": ("The user already has the test cases in `existing`, which run first, in order. "
                     "Propose 3 to 8 additional test cases for important documented behaviour they do not cover yet "
                     "(missing endpoints, error cases, edge cases). Do not repeat an existing case. "
                     "They run after the existing cases, so account for the state those leave behind: "
                     "prefer creating fresh resources with new unique names, and only use an id you can be sure of. "
                     "In description, say briefly why the case is worth adding."),
            "existing": [{k: c.get(k) for k in ("name", "method", "path", "payload", "expected_status")} for c in existing],
            "project": project,
            "schema": {"test_cases": [CASE_SCHEMA]},
        })
        return self._json(RULES, user)

    def analyze_failure(self, result: dict[str, Any]) -> dict[str, Any] | None:
        system = """You are a test failure analyst. Return JSON only. Explain only from the supplied result and evidence. Do not claim certainty when evidence is missing.
Return exactly: {"test_name": string, "severity": "high|medium|low", "root_cause": string, "evidence": [string], "suggested_action": string}"""
        user = json.dumps({"task": "Analyze this failed test", "result": result})
        return self._json(system, user)


llm = LLMService()
