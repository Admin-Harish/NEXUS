from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from .config import settings


TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
REPORT_DIR = Path("/app/data/reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def render_report(run: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html", "xml"]))
    html = env.get_template("report.html").render(run=run)
    path = REPORT_DIR / f"{run['id']}.html"
    path.write_text(html, encoding="utf-8")
    return f"{settings.backend_public_url}/reports/{run['id']}.html"
