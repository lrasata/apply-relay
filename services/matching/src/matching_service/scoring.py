"""Pure scoring logic, no AWS calls.

score = required skills count double, nice-to-have skills count once:

    score = 100 * (2 * required_matched + nice_matched) / (2 * required_total + nice_total)

so a candidate with every required skill and no extras scores ~77 for a typical job,
and nice-to-haves alone can never make a strong match.
"""

from dataclasses import dataclass

REQUIRED_WEIGHT = 2
NICE_WEIGHT = 1
STRONG_MATCH = 75
POSSIBLE_MATCH = 50


@dataclass(frozen=True)
class Job:
    job_id: str
    required_skills: tuple[str, ...]
    nice_to_have_skills: tuple[str, ...] = ()
    title: str = ""
    location: str = ""


@dataclass(frozen=True)
class Match:
    score: int
    matched_skills: list[str]
    missing_skills: list[str]
    recommendation: str


def recommendation_for(score: int) -> str:
    if score >= STRONG_MATCH:
        return "strong_match"
    if score >= POSSIBLE_MATCH:
        return "possible_match"
    return "weak_match"


def score(candidate_skills: list[str], job: Job) -> Match:
    have = {skill.lower() for skill in candidate_skills}
    required = list(dict.fromkeys(job.required_skills))
    nice = [s for s in dict.fromkeys(job.nice_to_have_skills) if s not in required]

    matched_required = [s for s in required if s in have]
    matched_nice = [s for s in nice if s in have]

    total = REQUIRED_WEIGHT * len(required) + NICE_WEIGHT * len(nice)
    earned = REQUIRED_WEIGHT * len(matched_required) + NICE_WEIGHT * len(matched_nice)
    # Round half up, so 74.5 becomes 75 (Python's round() would give 74).
    value = int(100 * earned / total + 0.5) if total else 0

    return Match(
        score=value,
        matched_skills=matched_required + matched_nice,
        # Only required skills are "missing": that's what a recruiter needs to see.
        missing_skills=[s for s in required if s not in have],
        recommendation=recommendation_for(value),
    )
