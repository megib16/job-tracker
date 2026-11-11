"""Conservative record matching. No email identifiers are persisted."""
from .models import Application

def matching_index(records: list[Application], incoming: Application) -> int | None:
    for index, current in enumerate(records):
        if current.company.casefold() != incoming.company.casefold():
            continue
        if current.job_id and incoming.job_id:
            if current.job_id.casefold() == incoming.job_id.casefold():
                return index
            continue
        if current.position.casefold() == incoming.position.casefold():
            return index
    return None
