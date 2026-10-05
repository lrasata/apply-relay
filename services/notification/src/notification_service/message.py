"""Builds the recruiter email from a CandidateMatched payload. No AWS calls here.

The event deliberately carries no candidate name or email, so neither does the email:
the recruiter opens the application by its ID. Less personal data in inboxes.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Email:
    subject: str
    text: str


def _skills(skills: list[str]) -> str:
    return ", ".join(skills) if skills else "none"


def build_email(match: dict) -> Email:
    subject = f"Strong match ({match['score']}/100) for {match['job_id']}"
    text = "\n".join(
        [
            f"A candidate scored {match['score']}/100 for job {match['job_id']}.",
            "",
            f"Application:     {match['application_id']}",
            f"Matched skills:  {_skills(match['matched_skills'])}",
            f"Missing skills:  {_skills(match['missing_skills'])}",
            f"Matched at:      {match['matched_at']}",
            "",
            "You get this email only for strong matches (score 75 or more).",
        ]
    )
    return Email(subject=subject, text=text)
