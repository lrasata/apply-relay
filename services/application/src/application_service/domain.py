"""Pure logic: request validation, IDs and the event envelope. No AWS calls here."""

import base64
import binascii
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

MAX_CV_BYTES = 2 * 1024 * 1024  # 2 MB; API Gateway and Lambda payload limits are higher, CVs aren't
ALLOWED_CV_TYPES = {"application/pdf": "pdf", "text/plain": "txt"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

EVENT_TYPE = "ApplicationSubmitted"
EVENT_VERSION = 1
PRODUCER = "application-service"


class ValidationError(Exception):
    """The request is malformed. Maps to HTTP 400."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class ApplicationRequest:
    full_name: str
    email: str
    job_id: str
    cv_content_type: str
    cv_bytes: bytes

    @property
    def cv_extension(self) -> str:
        return ALLOWED_CV_TYPES[self.cv_content_type]


def parse_request(body: object) -> ApplicationRequest:
    """Validate the JSON body of POST /applications. Collects every error, not just the first."""
    if not isinstance(body, dict):
        raise ValidationError(["body must be a JSON object"])

    errors = []

    full_name = body.get("full_name")
    if not isinstance(full_name, str) or not full_name.strip():
        errors.append("full_name is required")
    elif len(full_name) > 200:
        errors.append("full_name must be at most 200 characters")

    email = body.get("email")
    if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
        errors.append("email must be a valid email address")

    job_id = body.get("job_id")
    if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
        errors.append("job_id must be 1-64 letters, digits, '-' or '_'")

    cv = body.get("cv")
    cv_bytes = b""
    content_type = None
    if not isinstance(cv, dict):
        errors.append("cv is required: {content_type, content_base64}")
    else:
        content_type = cv.get("content_type")
        if content_type not in ALLOWED_CV_TYPES:
            errors.append(f"cv.content_type must be one of {sorted(ALLOWED_CV_TYPES)}")
        try:
            cv_bytes = base64.b64decode(cv.get("content_base64") or "", validate=True)
        except (binascii.Error, TypeError, ValueError):
            errors.append("cv.content_base64 must be valid base64")
        if not cv_bytes:
            errors.append("cv must not be empty")
        elif len(cv_bytes) > MAX_CV_BYTES:
            errors.append(f"cv must be at most {MAX_CV_BYTES // (1024 * 1024)} MB")
        elif content_type == "application/pdf" and not cv_bytes.startswith(b"%PDF-"):
            errors.append("cv is not a PDF file")

    if errors:
        raise ValidationError(errors)

    return ApplicationRequest(
        full_name=full_name.strip(),
        email=email.strip().lower(),
        job_id=job_id,
        cv_content_type=content_type,
        cv_bytes=cv_bytes,
    )


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def candidate_id_for(email: str) -> str:
    return f"cand_{_short_hash(email.lower())}"


def application_id_for(email: str, job_id: str) -> str:
    """Deterministic, so the same candidate applying twice to the same job gets the same ID."""
    return f"app_{_short_hash(email.lower() + '|' + job_id)}"


def cv_key_for(application_id: str, extension: str) -> str:
    return f"applications/{application_id}/cv.{extension}"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_event_id() -> str:
    return str(uuid.uuid4())


def build_event(application: dict) -> dict:
    """Build the ApplicationSubmitted envelope from a stored application item.

    The event_id and timestamps come from the stored item, so republishing after a crash
    produces the exact same event and consumers can de-duplicate it.
    """
    return {
        "event_id": application["event_id"],
        "event_type": EVENT_TYPE,
        "event_version": EVENT_VERSION,
        "occurred_at": application["submitted_at"],
        "correlation_id": application["application_id"],
        "producer": PRODUCER,
        "data": {
            "application_id": application["application_id"],
            "job_id": application["job_id"],
            "candidate": {
                "candidate_id": application["candidate_id"],
                "full_name": application["full_name"],
                "email": application["email"],
            },
            "cv": {
                "bucket": application["cv_bucket"],
                "key": application["cv_key"],
                "content_type": application["cv_content_type"],
            },
            "submitted_at": application["submitted_at"],
        },
    }
