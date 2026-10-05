import pytest

from matching_service.scoring import Job, recommendation_for, score

DEVOPS = Job(
    job_id="job_devops",
    required_skills=("aws", "terraform", "python", "docker", "kubernetes"),
    nice_to_have_skills=("github actions", "aws lambda", "prometheus"),
)


def test_everything_scores_100():
    match = score(list(DEVOPS.required_skills + DEVOPS.nice_to_have_skills), DEVOPS)
    assert match.score == 100
    assert match.missing_skills == []
    assert match.recommendation == "strong_match"


def test_all_required_and_no_extras_is_still_a_strong_match():
    match = score(["aws", "terraform", "python", "docker", "kubernetes"], DEVOPS)
    assert match.score == 77  # 10 / 13
    assert match.recommendation == "strong_match"


def test_example_from_the_contract():
    # docs/events/candidate-matched.schema.json example: 4 of 5 required + 2 nice-to-haves
    match = score(["aws", "terraform", "python", "docker", "github actions", "aws lambda", "java"], DEVOPS)
    assert match.score == 77  # 10 / 13
    assert match.matched_skills == ["aws", "terraform", "python", "docker", "github actions", "aws lambda"]
    assert match.missing_skills == ["kubernetes"]
    assert match.recommendation == "strong_match"


def test_one_required_skill_missing_and_no_extras_is_a_possible_match():
    match = score(["aws", "terraform", "python", "docker"], DEVOPS)
    assert match.score == 62  # 8 / 13
    assert match.recommendation == "possible_match"


def test_nice_to_haves_alone_are_a_weak_match():
    match = score(["github actions", "aws lambda", "prometheus"], DEVOPS)
    assert match.score == 23
    assert match.recommendation == "weak_match"
    assert match.missing_skills == list(DEVOPS.required_skills)


def test_no_skills_scores_zero():
    assert score([], DEVOPS).score == 0


def test_skill_listed_as_both_required_and_nice_counts_once_as_required():
    job = Job("j", required_skills=("aws",), nice_to_have_skills=("aws", "docker"))
    assert score(["aws"], job).score == 67  # 2 / 3


def test_job_without_skills_scores_zero_instead_of_dividing_by_zero():
    assert score(["aws"], Job("j", required_skills=())).score == 0


@pytest.mark.parametrize("value, expected", [(100, "strong_match"), (75, "strong_match"), (74, "possible_match"),
                                             (50, "possible_match"), (49, "weak_match"), (0, "weak_match")])
def test_recommendation_thresholds_match_the_contract(value, expected):
    assert recommendation_for(value) == expected
