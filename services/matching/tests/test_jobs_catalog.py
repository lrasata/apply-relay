"""jobs.json is matched against the CV parser's output, so every skill in it must be a
skill the parser can actually produce; a typo like "k8s" would silently never match."""

import importlib.util
import sys

import pytest

from conftest import JOBS, SERVICE_DIR

PARSER_MODULE = SERVICE_DIR.parent / "cv_parser" / "src" / "cv_parser_service" / "extraction.py"


@pytest.fixture(scope="module")
def parser_skills():
    pytest.importorskip("pypdf")  # the parser module imports it
    spec = importlib.util.spec_from_file_location("cv_parser_extraction", PARSER_MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return set(module.SKILLS)


@pytest.mark.parametrize("job", JOBS, ids=lambda job: job["job_id"])
def test_job_skills_are_known_to_the_cv_parser(job, parser_skills):
    unknown = set(job["required_skills"]) | set(job["nice_to_have_skills"])
    assert unknown - parser_skills == set()


def test_job_ids_are_unique():
    ids = [job["job_id"] for job in JOBS]
    assert len(ids) == len(set(ids))
