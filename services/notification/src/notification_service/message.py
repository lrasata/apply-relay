"""Builds the recruiter email from a CandidateMatched payload. No AWS calls here.

The event deliberately carries no candidate name or email, so neither does the email:
the recruiter opens the application (and the CV) from the links. Less personal data in inboxes.

job_title, job_location, years_of_experience and languages were added to the event after v1
shipped, so every one of them is optional here.
"""

import html
from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class Email:
    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class Links:
    """AWS console links for the recruiter. Either may be empty when not configured."""

    cv: str = ""
    application: str = ""


def console_links(application_id: str, region: str, cvs_bucket: str, applications_table: str) -> Links:
    console = f"https://{region}.console.aws.amazon.com"
    cv = (
        f"https://s3.console.aws.amazon.com/s3/buckets/{cvs_bucket}"
        f"?region={region}&prefix={quote(f'applications/{application_id}/')}"
        if cvs_bucket
        else ""
    )
    application = (
        f"{console}/dynamodbv2/home?region={region}#edit-item?itemMode=2"
        f"&pk={quote(application_id)}&route=ROUTE_ITEM_EXPLORER&sk=&table={quote(applications_table)}"
        if applications_table
        else ""
    )
    return Links(cv=cv, application=application)


def _job(match: dict) -> str:
    title = match.get("job_title") or match["job_id"]
    location = match.get("job_location")
    return f"{title}, {location}" if location else title


def _years(value: float | None) -> str:
    if value is None:
        return "not stated in the CV"
    number = f"{value:g}"
    return f"{number} year" if number == "1" else f"{number} years"


def _list(values: list[str], capitalize: bool = False) -> str:
    if not values:
        return "none"
    return ", ".join(v.capitalize() if capitalize else v for v in values)


def _when(timestamp: str) -> str:
    # "2026-10-05T18:40:05Z" -> "2026-10-05 18:40 UTC"
    return f"{timestamp[:10]} {timestamp[11:16]} UTC" if len(timestamp) >= 16 else timestamp


def _verdict(missing: list[str]) -> str:
    if not missing:
        return "Has every required skill."
    return f"Missing {len(missing)} required skill{'s' if len(missing) > 1 else ''}: {', '.join(missing)}"


def build_email(match: dict, links: Links = Links()) -> Email:
    job = _job(match)
    score = match["score"]
    missing = match["missing_skills"]
    rows = [
        ("Score", f"{score}/100 (75 or more is a strong match)"),
        ("Required skills", _verdict(missing)),
        ("Matched skills", _list(match["matched_skills"])),
        ("Experience", _years(match.get("years_of_experience"))),
        ("Languages", _list(match.get("languages", []), capitalize=True)),
    ]
    footer = (
        f"Application {match['application_id']} for {match['job_id']}, matched {_when(match['matched_at'])}. "
        "You only get these emails for strong matches. The candidate's name and contact details "
        "are left out on purpose: they're in the application."
    )

    text_lines = [f"Strong match for {job}", ""]
    text_lines += [f"{label + ':':<17}{value}" for label, value in rows]
    text_lines += ["", "NEXT STEP", "Review the CV and the application:"]
    if links.cv:
        text_lines.append(f"  CV:           {links.cv}")
    if links.application:
        text_lines.append(f"  Application:  {links.application}")
    if not (links.cv or links.application):
        text_lines.append(f"  look up application {match['application_id']}")
    text_lines += ["", footer]

    e = html.escape
    table = "".join(
        f'<tr><td style="padding:4px 16px 4px 0;color:#666;white-space:nowrap">{e(label)}</td>'
        f'<td style="padding:4px 0">{e(value)}</td></tr>'
        for label, value in rows
    )
    buttons = "".join(
        f'<a href="{e(url)}" style="display:inline-block;margin:0 8px 8px 0;padding:8px 14px;'
        f'background:#1a73e8;color:#fff;text-decoration:none;border-radius:4px">{e(label)}</a>'
        for label, url in (("Open the CV", links.cv), ("Open the application", links.application))
        if url
    )
    body_html = (
        '<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;max-width:560px">'
        f'<p style="margin:0 0 4px;color:#1e8e3e;font-weight:bold">Strong match: {score}/100</p>'
        f'<h2 style="margin:0 0 16px;font-size:20px">{e(job)}</h2>'
        f'<table style="border-collapse:collapse;margin-bottom:16px">{table}</table>'
        f"<p>{buttons}</p>"
        f'<p style="color:#888;font-size:12px">{e(footer)}</p>'
        "</div>"
    )

    return Email(subject=f"Strong match {score}/100: {job}", text="\n".join(text_lines), html=body_html)
