"""Keyword-based CV parsing (v1). Pure Python apart from pypdf; no AWS calls here.

Deliberately simple and explainable: every skill found can be traced back to a pattern
below. Swapping this module for an LLM or Amazon Comprehend later doesn't change the
event contract.
"""

import io
import re
from dataclasses import dataclass
from datetime import date

from pypdf import PdfReader

# Canonical skill name -> regex alternatives (matched case-insensitively on word boundaries).
SKILLS = {
    "aws": [r"aws", r"amazon web services"],
    "aws lambda": [r"aws lambda", r"lambda functions?"],
    "dynamodb": [r"dynamo ?db"],
    "s3": [r"s3"],
    "cloudformation": [r"cloudformation"],
    "aws cdk": [r"aws cdk", r"cdk"],
    "azure": [r"azure"],
    "gcp": [r"gcp", r"google cloud"],
    "terraform": [r"terraform"],
    "ansible": [r"ansible"],
    "docker": [r"docker"],
    "kubernetes": [r"kubernetes", r"k8s", r"eks", r"aks", r"gke"],
    "helm": [r"helm"],
    "linux": [r"linux"],
    "bash": [r"bash", r"shell scripting"],
    "git": [r"git"],
    "github actions": [r"github actions"],
    "gitlab ci": [r"gitlab[ -]ci"],
    "jenkins": [r"jenkins"],
    "ci/cd": [r"ci/cd", r"continuous (?:integration|delivery|deployment)"],
    "prometheus": [r"prometheus"],
    "grafana": [r"grafana"],
    "python": [r"python"],
    "java": [r"java"],
    "javascript": [r"javascript", r"js"],
    "typescript": [r"typescript"],
    "node.js": [r"node\.?js"],
    "react": [r"react(?:\.?js)?"],
    "go": [r"golang"],
    "c#": [r"c#"],
    "sql": [r"sql"],
    "postgresql": [r"postgres(?:ql)?"],
    "mongodb": [r"mongo ?db"],
    "kafka": [r"kafka"],
    "rabbitmq": [r"rabbit ?mq"],
    "microservices": [r"micro-?services?"],
    "event-driven architecture": [r"event[- ]driven"],
}

LANGUAGES = {
    "english": [r"english", r"englisch"],
    "german": [r"german", r"deutsch"],
    "french": [r"french", r"fran[cç]ais", r"franz[oö]sisch"],
    "spanish": [r"spanish", r"espa[nñ]ol"],
    "italian": [r"italian", r"italiano"],
    "portuguese": [r"portuguese", r"portugu[eê]s"],
    "dutch": [r"dutch", r"nederlands"],
    "polish": [r"polish", r"polski"],
    "russian": [r"russian"],
    "chinese": [r"chinese", r"mandarin"],
    "japanese": [r"japanese"],
    "arabic": [r"arabic"],
    "hindi": [r"hindi"],
    "turkish": [r"turkish"],
    "malagasy": [r"malagasy"],
}


def _compile(table: dict) -> dict:
    # (?<![\w#.]) / (?![\w#+]) instead of \b so "c#" and "node.js" match, "java" doesn't match "javascript".
    return {
        name: re.compile(r"(?<![\w#.])(?:" + "|".join(patterns) + r")(?![\w#+])", re.IGNORECASE)
        for name, patterns in table.items()
    }


SKILL_PATTERNS = _compile(SKILLS)
LANGUAGE_PATTERNS = _compile(LANGUAGES)

# "5 years of experience", "7+ yrs experience", "5 Jahre Berufserfahrung"
EXPLICIT_YEARS = re.compile(
    r"(\d{1,2}(?:[.,]\d)?)\s*\+?\s*(?:years?|yrs?|jahre)\b[^.\n]{0,30}?(?:experience|erfahrung)",
    re.IGNORECASE,
)
# "2019 - 2023", "03/2018 – present", "2020 - heute"
DATE_RANGE = re.compile(
    r"(?:\d{1,2}/)?((?:19|20)\d{2})\s*(?:-|–|—|to|bis)\s*(?:(?:\d{1,2}/)?((?:19|20)\d{2})|(present|current|now|today|heute))",
    re.IGNORECASE,
)
MAX_YEARS = 50


@dataclass(frozen=True)
class ParsedCv:
    skills: list[str]
    years_of_experience: float | None
    languages: list[str]


class UnreadableCv(Exception):
    """The file can't be turned into text (corrupt or encrypted PDF). Retrying won't help."""


def extract_text(content: bytes, content_type: str) -> str:
    if content_type == "text/plain":
        return content.decode("utf-8", errors="replace")
    if content_type == "application/pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:  # pypdf raises many different error types
            raise UnreadableCv(str(exc)) from exc
    raise UnreadableCv(f"unsupported content type {content_type}")


def find_skills(text: str) -> list[str]:
    return sorted(name for name, pattern in SKILL_PATTERNS.items() if pattern.search(text))


def find_languages(text: str) -> list[str]:
    return sorted(name for name, pattern in LANGUAGE_PATTERNS.items() if pattern.search(text))


def estimate_years(text: str, today: date | None = None) -> float | None:
    """An explicit "N years of experience" wins; otherwise the span from the earliest job
    start to the latest end date. None when the CV gives neither."""
    explicit = [float(m.group(1).replace(",", ".")) for m in EXPLICIT_YEARS.finditer(text)]
    if explicit:
        return min(max(explicit), MAX_YEARS)

    this_year = (today or date.today()).year
    starts, ends = [], []
    for start, end, ongoing in DATE_RANGE.findall(text):
        start_year = int(start)
        end_year = this_year if ongoing else int(end)
        if start_year <= end_year <= this_year:
            starts.append(start_year)
            ends.append(end_year)
    if not starts:
        return None
    return float(min(max(ends) - min(starts), MAX_YEARS))


def parse_cv(content: bytes, content_type: str, today: date | None = None) -> ParsedCv:
    text = extract_text(content, content_type)
    return ParsedCv(
        skills=find_skills(text),
        years_of_experience=estimate_years(text, today),
        languages=find_languages(text),
    )
