import base64

import pytest

from application_service import domain
from conftest import PDF_BYTES, make_body


def test_application_id_is_deterministic_and_case_insensitive_on_email():
    a = domain.application_id_for("alex@example.com", "job_1")
    assert a == domain.application_id_for("ALEX@example.com", "job_1")
    assert a != domain.application_id_for("alex@example.com", "job_2")
    assert a.startswith("app_") and len(a) == 16


def test_parse_request_normalises_email_and_decodes_cv():
    request = domain.parse_request(make_body())
    assert request.email == "alex@example.com"
    assert request.cv_bytes == PDF_BYTES
    assert request.cv_extension == "pdf"


def test_parse_request_reports_every_error_at_once():
    with pytest.raises(domain.ValidationError) as exc:
        domain.parse_request({"full_name": " ", "email": "not-an-email", "job_id": "bad id!"})
    assert len(exc.value.errors) == 4  # name, email, job_id, cv


@pytest.mark.parametrize(
    "cv, message",
    [
        ({"content_type": "image/png", "content_base64": "aGVsbG8="}, "content_type"),
        ({"content_type": "text/plain", "content_base64": "%%%"}, "base64"),
        ({"content_type": "text/plain", "content_base64": ""}, "empty"),
        ({"content_type": "application/pdf", "content_base64": "aGVsbG8="}, "not a PDF"),
        (
            {
                "content_type": "text/plain",
                "content_base64": base64.b64encode(b"x" * (domain.MAX_CV_BYTES + 1)).decode(),
            },
            "at most",
        ),
    ],
)
def test_parse_request_rejects_bad_cvs(cv, message):
    with pytest.raises(domain.ValidationError) as exc:
        domain.parse_request(make_body(cv=cv))
    assert any(message in error for error in exc.value.errors)


def test_build_event_matches_the_published_contract(schemas):
    item = {
        "application_id": "app_5f1d7a0c9e2b",
        "job_id": "job_1",
        "candidate_id": "cand_8a3e19",
        "full_name": "Alex Example",
        "email": "alex@example.com",
        "cv_bucket": "bucket",
        "cv_key": "applications/app_5f1d7a0c9e2b/cv.pdf",
        "cv_content_type": "application/pdf",
        "submitted_at": "2026-10-05T18:40:00Z",
        "event_id": domain.new_event_id(),
    }
    envelope = domain.build_event(item)
    schemas["envelope"].validate(envelope)
    schemas["application-submitted"].validate(envelope["data"])
