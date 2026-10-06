import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from pathlib import Path
from .config import settings


def _summary_html(run: dict) -> str:
    results = run.get("results", [])
    passed = sum(1 for r in results if r.get("status") == "passed")
    failed = sum(1 for r in results if r.get("status") == "failed")
    target = run.get("target", {})
    colour = "#14804a" if run["status"] == "passed" else "#c0392b"
    rows = "".join(
        f"<tr><td style='padding:6px 10px;border-bottom:1px solid #eee'>{escape(str(r.get('test_name')))}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #eee'><code>{escape(str(r.get('method', '')))} {escape(str(r.get('path', '')))}</code></td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #eee;color:{'#14804a' if r.get('status') == 'passed' else '#c0392b'}'><b>{escape(str(r.get('status')))}</b></td></tr>"
        for r in results
    )
    return f"""<div style="font-family:Arial,sans-serif;color:#172033">
<h2 style="margin:0 0 8px">NEXUS test report – {escape(target.get('name', 'run'))}</h2>
<p style="margin:0 0 4px">Overall: <b style="color:{colour}">{escape(run['status'].upper())}</b> &nbsp;·&nbsp; {passed} passed, {failed} failed, {len(results)} total</p>
<p style="margin:0 0 12px;color:#667085">Source: {escape(target.get('source', ''))} {escape(target.get('commit', ''))}</p>
<table style="border-collapse:collapse;font-size:14px">{rows}</table>
<p style="color:#667085">The full HTML report and the test cases spreadsheet (with results) are attached. Run ID: {escape(run['id'])}</p></div>"""


def send_report(run: dict, to_email: str | None = None, report_path: Path | None = None, excel: bytes | None = None) -> tuple[bool, str]:
    recipient = to_email or settings.report_to_email
    if not settings.gmail_username or not settings.gmail_app_password or not recipient:
        return False, "Gmail settings or recipient are missing."
    target = run.get("target", {}).get("name", "")
    message = EmailMessage()
    message["Subject"] = f"NEXUS test report: {target} – {run['status'].upper()}".replace(":  –", ":")
    message["From"] = formataddr(("NEXUS – Harish Dayalan", settings.gmail_username))
    message["To"] = recipient
    message.set_content(f"NEXUS run {run['id']} completed with status: {run['status']}.\nThe HTML report is attached.")
    message.add_alternative(_summary_html(run), subtype="html")
    if report_path and report_path.is_file():
        message.add_attachment(report_path.read_bytes(), maintype="text", subtype="html", filename=f"nexus-report-{run['id'][:8]}.html")
    if excel:
        message.add_attachment(excel, maintype="application", subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               filename=f"nexus-testcases-{target or 'run'}-{run['id'][:8]}.xlsx")
    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.login(settings.gmail_username, settings.gmail_app_password)
            smtp.send_message(message)
        return True, f"Report emailed to {recipient} from {settings.gmail_username}."
    except Exception as exc:
        return False, f"Email delivery failed: {exc}"
