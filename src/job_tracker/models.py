from datetime import date
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Status(str, Enum):
    APPLIED = "applied"
    UNDER_REVIEW = "under_review"
    ADDITIONAL_INFORMATION = "additional_information_requested"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    WITHDRAWN = "withdrawn"
    UNKNOWN = "unknown"

class Application(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    company: str = Field(min_length=1, max_length=160)
    position: str = Field(min_length=1, max_length=200)
    job_id: str | None = Field(default=None, max_length=100)
    status: Status
    application_date: date | None = None
    rejection_date: date | None = None

    @model_validator(mode="after")
    def validate_rejection(self):
        if self.rejection_date and self.status != Status.REJECTED:
            raise ValueError("rejection_date requires rejected status")
        if self.application_date and self.rejection_date and self.rejection_date < self.application_date:
            raise ValueError("rejection date cannot precede application date")
        return self


class ApplicationWithNotes(Application):
    notes: str = Field(min_length=1, max_length=240)
