"""Recruitment Domain Event Handler exports."""

from talent_screener.application.recruitment.event_handlers.lock_rubric_handler import (
    LockRubricOnBatchOrInterviewStartedHandler,
)

__all__ = ["LockRubricOnBatchOrInterviewStartedHandler"]
