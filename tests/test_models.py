import pytest
from pydantic import ValidationError
from job_tracker.models import Application, Status
from job_tracker.matcher import matching_index

def test_minimal_record():
    record = Application(company="Example GmbH", position="AI Intern", status="applied")
    assert record.status == Status.APPLIED
    assert record.job_id is None

def test_rejects_extra_personal_data():
    with pytest.raises(ValidationError):
        Application(company="Example", position="Intern", status="applied", recruiter_name="Person")

def test_rejection_date_requires_rejection():
    with pytest.raises(ValidationError):
        Application(company="Example", position="Intern", status="applied", rejection_date="2026-11-09")

def test_matching_by_job_id():
    records = [Application(company="Example", position="AI Intern", job_id="JR-42", status="applied")]
    incoming = Application(company="example", position="AI Internship", job_id="jr-42", status="interview")
    assert matching_index(records, incoming) == 0
