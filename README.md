# NEXUS

NEXUS is an adapter-based E2E test planning and execution workspace.

It supports:

- Three test modes against any Git repository: automatic discovery, a custom scenario, or an Excel sheet of test cases
- Suggested extra test cases you can tick, exported back to Excel with results
- Git repository analysis, container deployment and live test execution
- GPT-4 mini execution-plan and test-case generation
- A human approval gate before anything is deployed
- Automatic cleanup of target containers after each run
- Natural failure analysis when a test fails
- HTML reports
- Automatic Gmail delivery of the report (with the HTML attached)
- Flutter web frontend
- FastAPI backend

## Quick start

1. Copy the environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your values:

   ```env
   OPENAI_API_KEY=your-openai-api-key
   GMAIL_USERNAME=your-gmail-address@gmail.com
   GMAIL_APP_PASSWORD=your-16-character-gmail-app-password
   REPORT_TO_EMAIL=recipient@example.com
   ```

   The application still runs without these values using deterministic fallback generation. Gmail uses an app password, not your normal Gmail password.

3. Start everything:

   ```bash
   docker compose up --build
   ```

4. Open the Flutter UI:

   http://localhost:8080

5. Open the API documentation:

   http://localhost:8000/docs

## Demo flow

1. Open http://localhost:8080 and paste a Git clone link, or click a sample repo:
   - `https://github.com/Admin-Harish/Project1.git` – Volume Manager API
   - `https://github.com/Admin-Harish/Project2.git` – Inventory API
2. Choose **what to test**:
   - **Discover automatically** – GPT reads the README, Dockerfile, OpenAPI file and routes,
     and proposes test cases for the main features and error cases.
   - **Custom scenario** – describe a flow in plain English (see `samples/*_scenario.txt`);
     NEXUS turns it into test cases against that repository.
   - **Excel test cases** – upload an `.xlsx` or `.csv` of test cases to run against the
     repository. Columns such as `ID, Test case, Method, Path, Body, Expected status,
     Expected text` are used as is (`samples/project1_testcases.xlsx`); plain-English rows are
     interpreted by GPT (`samples/project2_requirements.xlsx`). NEXUS also suggests test cases
     the sheet is missing.
3. Choose the **test framework**: `pytest` (pytest + requests) or `Robot Framework`
   (RequestsLibrary).
4. Review the plan. Every test case has a checkbox: spreadsheet rows start ticked and
   suggested additions start unticked, so you choose which to add. Click **Approve N, deploy & run**.
5. NEXUS **generates a real test suite** from the approved test cases, builds the repository's
   `Dockerfile`, starts the container on the `nexus_net` network, waits until it answers HTTP,
   and **runs the generated suite** against it with pytest or Robot Framework. Stages, results
   and the framework's console output update live.
6. The target container, its image and the cloned source are then removed; only the NEXUS
   backend and frontend stay running.
7. The HTML report, a results spreadsheet and the test suite (zip) are emailed from `GMAIL_USERNAME` to the address
   you gave (or `REPORT_TO_EMAIL`). The spreadsheet lists every executed case with its result
   and marks the ones NEXUS added. The Report page has **Download report**, **Download test
   suite**, **Download Excel**, **Open report**, **Back to home**, a preview of the generated
   script, and **Email report copy to** for sending a copy to other people.

## Generated test suites

Every run writes its suite to `generated_testsuites/<YYYYMMDD-HHMMSS>_<repo>_<framework>/`
(timestamps use `NEXUS_TIMEZONE`, default `Asia/Kolkata`):

```
generated_testsuites/20261006-133535_project1_pytest/
├── tests/test_project1.py      # one test function per approved case
├── tests/conftest.py           # streams results to NEXUS while it runs
├── pytest.ini, requirements.txt, README.md
├── test_cases.json             # the approved test cases
└── results/                    # junit.xml (or Robot output.xml, log.html, report.html),
                                # nexus_report.html, nexus_results.xlsx
```

The suites are self-contained: start the service yourself and run
`BASE_URL=http://localhost:8000 pytest -v` (or
`robot --variable BASE_URL:http://localhost:8000 <repo>.robot`).

## Requirements for a repository

- Public GitHub or GitLab repository.
- A `Dockerfile` at the root that starts an HTTP service. The port is read from its
  `EXPOSE` line (default 8000).
- Endpoints documented in the README, an OpenAPI file, or discoverable route decorators,
  so the generated tests use real paths.

## Project layout

- `frontend/`: Flutter web application (staged flow)
- `backend/`: FastAPI orchestration service; `deployer.py` builds and runs targets,
  `pipeline.py` drives each run, `generator.py` writes the pytest / Robot suites,
  `executor.py` runs them, `excel.py` reads and writes spreadsheets
- `generated_testsuites/`: the generated pytest / Robot Framework suites (git-ignored)
- `samples/`: sample scenarios and spreadsheets for Project1 and Project2
- `data/`: generated reports

## Safety notes

- The LLM generates structured plans; it does not receive unrestricted shell access.
- The deterministic runner only executes allowlisted API actions.
- Repository analysis is read-only.
- The backend mounts the Docker socket so it can build and run targets. That gives it full control of the local Docker engine: run NEXUS only on a machine you trust, and only test repositories you trust.
- Target containers are limited to 512 MB of memory and one CPU, but this is not production security isolation.
- Add authentication, persistent storage, secret management, audit logging, and stronger container isolation before production use.
