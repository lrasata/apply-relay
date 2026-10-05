from datetime import date

import pytest

from cv_parser_service.extraction import (
    UnreadableCv,
    estimate_years,
    find_languages,
    find_skills,
    parse_cv,
)
from pdfs import make_pdf

TODAY = date(2026, 10, 5)


def test_skills_are_canonical_sorted_and_unique():
    text = "AWS, Amazon Web Services, Terraform, terraform, K8s and Kubernetes, Postgres"
    assert find_skills(text) == ["aws", "kubernetes", "postgresql", "terraform"]


@pytest.mark.parametrize(
    "text, present, absent",
    [
        ("JavaScript and TypeScript", {"javascript", "typescript"}, {"java"}),
        ("Java 17, Spring", {"java"}, {"javascript"}),
        ("C# and .NET", {"c#"}, set()),
        ("Node.js services", {"node.js"}, {"javascript"}),
        ("GitHub Actions pipelines", {"github actions"}, {"git"}),
        ("Git, GitLab CI", {"git", "gitlab ci"}, set()),
        ("PostgreSQL and MySQL", {"postgresql"}, {"sql"}),
        ("let's go to the reactive meeting", set(), {"go", "react"}),
        ("event-driven microservices", {"event-driven architecture", "microservices"}, set()),
    ],
)
def test_skill_boundaries(text, present, absent):
    found = set(find_skills(text))
    assert present <= found
    assert not (absent & found)


def test_languages_in_english_and_native_names():
    assert find_languages("Languages: English (C1), Deutsch (B2), Français, Malagasy") == [
        "english",
        "french",
        "german",
        "malagasy",
    ]


@pytest.mark.parametrize(
    "text, years",
    [
        ("Cloud engineer with 5 years of experience", 5.0),
        ("7+ yrs professional experience", 7.0),
        ("3 years experience in AWS, 8 years of experience overall", 8.0),
        ("5 Jahre Berufserfahrung", 5.0),
        ("Engineer at A, 2016 - 2019. Engineer at B, 03/2019 – present", 10.0),
        ("Intern 2024 to 2025", 1.0),
        ("Founded in 2015, our team...", None),
        ("No dates here", None),
    ],
)
def test_estimate_years(text, years):
    assert estimate_years(text, today=TODAY) == years


def test_future_date_ranges_are_ignored():
    assert estimate_years("Planned 2027 - 2030", today=TODAY) is None


def test_parse_pdf_cv():
    pdf = make_pdf(
        [
            "Alex Example - DevOps Engineer",
            "6 years of experience building on AWS",
            "Skills: Terraform, Python, Docker, GitHub Actions",
            "Languages: English, German",
        ]
    )
    parsed = parse_cv(pdf, "application/pdf", today=TODAY)
    assert parsed.skills == ["aws", "docker", "github actions", "python", "terraform"]
    assert parsed.years_of_experience == 6.0
    assert parsed.languages == ["english", "german"]


def test_corrupt_pdf_is_unreadable():
    with pytest.raises(UnreadableCv):
        parse_cv(b"%PDF-1.4 this is not really a pdf", "application/pdf")
