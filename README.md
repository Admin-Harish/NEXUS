# NEXUS

**Next-generation Environment eXecution & Unified System Testing**

From Git repo to deployed, tested and reported in minutes. GPT designs the test cases, a
person approves them, and NEXUS generates and runs a real pytest or Robot Framework suite
against the repository running in its own container.

## Features

- **Three test modes against any Git repository**: automatic discovery, a custom scenario in
  plain English, or an Excel/CSV sheet of test cases
- **Gap-finding for Excel**: NEXUS suggests test cases your sheet is missing; you tick which to
  add, and they are written back to the exported sheet marked "Added by NEXUS"
- **Human approval gate**: nothing is deployed until you approve the selected test cases
- **Real test code**: approved cases become a pytest (requests) or Robot Framework
  (RequestsLibrary) suite, saved under `generated_testsuites/` with a timestamp
- **Disposable environments**: each run clones the repo, builds its `Dockerfile`, runs the
  suite against the container, then removes the container, image and clone
- **Live progress**: stages, per-test results and the framework's console output stream to the UI
- **Evidence**: HTML report, Excel results, framework output (JUnit XML or Robot
  output.xml/log.html/report.html) and GPT failure analysis for failed tests
- **Email**: the report, Excel results and suite zip are emailed automatically; the last stage
  can send copies to other recipients
- Flutter web frontend, FastAPI backend, OpenAI `gpt-4o-mini`

## Quick start

Prerequisites: Docker (Docker Desktop, Colima or OrbStack) with Compose, and a free port
8000 and 8080.

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

   Gmail needs an [app password](https://myaccount.google.com/apppasswords), not your normal
   password. Without an OpenAI key NEXUS still runs, but only generates a basic health check.

3. Start NEXUS:

   ```bash
   docker compose up --build -d
   ```

4. Open the UI at http://localhost:8080 (API docs: http://localhost:8000/docs).

## Demo script

About 5 minutes. The sample targets are two small APIs built for NEXUS:

| Repo | What it is | Port |
|---|---|---|
| https://github.com/Admin-Harish/Project1.git | Volume Manager API: create, read, resize, delete volumes; capacity stats | 8000 |
| https://github.com/Admin-Harish/Project2.git | Inventory API: add, search, update stock, delete items | 8001 |

### Before you start

1. Start NEXUS and confirm only NEXUS is running:

   ```bash
   docker compose up -d && docker compose ps
   ```

2. Open http://localhost:8080 and keep `samples/` and `generated_testsuites/` open in your editor.
3. Optional: do one dry run of Demo 1 beforehand. The first build downloads the Python base
   image, so it is slower; later runs take under a minute. Keep a finished run open in a
   second browser tab as a backup.

### Demo 1: Discover tests automatically (Project1, Robot Framework)

1. Click the **Project1 · Volume API** chip to fill in the clone link.
2. Leave **What to test** on **Discover automatically**.
3. Set **Test framework** to **Robot Framework**, then click **Start**.
4. **Analyze**: NEXUS clones the repo read-only and GPT reads the README, Dockerfile and routes.
5. **Approve plan**: point out the detected stack, the evidence, and the 8–10 test cases,
   including error cases (duplicate name → 409, unknown volume → 404, shrinking → 400).
   Untick one case to show selection, tick it again, then click **Approve N, deploy & run**.
6. **Deploy & test**: walk through the stages as they turn green: clone, generate test suite,
   build image, start container, run the Robot suite (results appear one by one), remove
   containers, report, email.
7. **Report**:
   - Expand **Generated Robot Framework suite** to show the real `.robot` code.
   - Click **Download test suite**, **Download Excel** and **Open report**.
   - Type a colleague's address in **Email report copy to** and click **Send copy**.
8. In your editor, open the new folder `generated_testsuites/<timestamp>_project1_robot/` and
   show `project1.robot` and `results/log.html`.

### Demo 2: Excel test cases with suggestions (Project2, pytest)

1. Click **Back to home**, then the **Project2 · Inventory API** chip.
2. Choose **Excel test cases** and upload `samples/project2_requirements.xlsx` (5
   plain-English rows such as "Adding 'laptop' again must be rejected").
3. Keep **pytest**, then click **Start**.
4. **Approve plan**: the 5 rows appear under **From your spreadsheet** (ticked, with their IDs
   INV-1 to INV-5) and NEXUS's ideas under **Suggested additions** (unticked). Tick 2–3
   suggestions, for example the negative-quantity and unknown-ID cases, then approve.
5. On the **Report** page click **Download Excel**: your rows keep their IDs, and the
   suggestions you added appear as NX-1, NX-2… with "Added by NEXUS" and their results.

For a structured sheet, use `samples/project1_testcases.xlsx` with Project1. Its columns
(ID, Test case, Method, Path, Body, Expected status, Expected text) are used exactly as written.

### Demo 3 (optional): Custom scenario (Project1)

1. **Back to home** → **Project1** → **Custom scenario**.
2. Paste the text from `samples/project1_scenario.txt`:

   > A storage admin provisions a 100 GB volume called "analytics", grows it to 250 GB,
   > confirms that trying to shrink it back to 100 GB is refused, checks that total capacity in
   > the stats is 250 GB, then deletes the volume and confirms it is gone.

3. Start, approve, and show that the generated tests follow the scenario step by step.

### Prove the suite stands on its own (optional)

The generated suite does not need NEXUS. Start Project1 yourself and run it:

```bash
git clone https://github.com/Admin-Harish/Project1.git /tmp/Project1 && docker build -t project1 /tmp/Project1
```

```bash
docker run -d --rm --name project1 -p 8000:8000 project1
```

Then, inside a `generated_testsuites/<timestamp>_project1_pytest/` folder:

```bash
pip install -r requirements.txt && BASE_URL=http://localhost:8000 pytest -v
```

Stop NEXUS's port 8000 first, or map Project1 to another port (`-p 18000:8000`) and use that
port in `BASE_URL`. When you're done:

```bash
docker rm -f project1
```

### Wrap-up checks

- `docker ps` shows only `nexus_project-backend-1` and `nexus_project-frontend-1`: every target
  container was removed.
- Your inbox has a report email per run, from `GMAIL_USERNAME`, with the HTML report, Excel
  results and suite zip attached.

### Troubleshooting

| Symptom | Fix |
|---|---|
| "Could not clone …" | The repo must be public and the link must end in the repo name (`.git` optional). |
| "No Dockerfile found …" | NEXUS needs a `Dockerfile` at the repository root. |
| "Service did not answer on port …" | Check the Dockerfile `EXPOSE` line matches the port the app listens on. |
| Email not sent | Check `GMAIL_USERNAME` / `GMAIL_APP_PASSWORD` in `.env`, then `docker compose up -d` to reload. |
| A test fails unexpectedly | Read the GPT failure analysis on the Report page and the test code in the suite; the plan may assume state that an earlier test changed. |

## How a run works

1. **Input**: a Git clone link, a test mode and a framework.
2. **Analyze**: clone read-only; read README, Dockerfile, OpenAPI file and route definitions;
   GPT proposes an execution plan (Excel rows with structured columns are mapped without GPT).
3. **Approve plan**: tick the test cases to run.
4. **Deploy & test**: generate the suite, build the `Dockerfile`, start the container on the
   `nexus_net` network, wait until it answers HTTP, run the suite, remove the container.
5. **Report**: HTML report, Excel results and suite zip; emailed automatically to
   `REPORT_TO_EMAIL`, with optional copies to others.

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

GPT designs the test cases; NEXUS writes the scripts from fixed templates, so the code is
predictable and reviewable. Tests run in file order and share the service's state, exactly as
NEXUS ran them. Re-run a suite with `BASE_URL=http://localhost:8000 pytest -v` or
`robot --variable BASE_URL:http://localhost:8000 <repo>.robot`.

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
  `executor.py` runs them, `excel.py` reads and writes spreadsheets, `llm.py` holds the prompts
- `generated_testsuites/`: the generated pytest / Robot Framework suites (git-ignored)
- `samples/`: sample scenarios and spreadsheets for Project1 and Project2
- `data/`: generated HTML reports and suite archives (git-ignored)

## Safety notes

- GPT only returns structured test plans. It never gets shell access, and the generated scripts
  only send HTTP requests to the target container.
- Nothing is deployed or executed before a person approves the plan.
- Repository analysis is read-only.
- The backend mounts the Docker socket so it can build and run targets. That gives it full
  control of the local Docker engine: run NEXUS only on a machine you trust, and only test
  repositories you trust.
- Target containers are limited to 512 MB of memory and one CPU, but this is not production
  security isolation.
- Add authentication, persistent storage, secret management, audit logging and stronger
  container isolation before production use.
