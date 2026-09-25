from typing import Literal, Self
from zoneinfo import available_timezones

from pydantic import BaseModel, Field, field_validator, model_validator

QuestionType = Literal["single", "select_two"]
Confidence = Literal["confident", "educated_guess", "complete_guess"]

# (option count, correct count) per question type
QUESTION_SHAPES: dict[str, tuple[int, int]] = {"single": (4, 1), "select_two": (5, 2)}


class DeckCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    parent_id: int | None = None
    session_size: int = Field(default=20, ge=1, le=500)
    new_per_day: int = Field(default=20, ge=0, le=1000)


class DeckUpdate(BaseModel):
    """Partial update: only fields present in the payload change. parent_id null = top level."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    parent_id: int | None = None
    session_size: int | None = Field(default=None, ge=1, le=500)
    new_per_day: int | None = Field(default=None, ge=0, le=1000)


class OptionIn(BaseModel):
    id: str | None = Field(default=None, description="Existing option id to keep (updates only). Omit for new options.")
    text: str = Field(min_length=1, max_length=1000)
    correct: bool = False
    explanation: str | None = Field(default=None, max_length=2000, description="Why this option is right or wrong.")


class QuestionIn(BaseModel):
    type: QuestionType = Field(description='"single" = 4 options, 1 correct. "select_two" = 5 options, 2 correct.')
    stem: str = Field(min_length=1, max_length=5000, description="The question, Markdown.")
    options: list[OptionIn]
    explanation: str | None = Field(default=None, max_length=5000, description="Overall explanation, Markdown.")

    @model_validator(mode="after")
    def check_shape(self) -> Self:
        option_count, correct_count = QUESTION_SHAPES[self.type]
        if len(self.options) != option_count:
            raise ValueError(f"A {self.type} question needs exactly {option_count} options.")
        if sum(o.correct for o in self.options) != correct_count:
            raise ValueError(f"A {self.type} question needs exactly {correct_count} correct option(s).")
        if len({o.text.strip().lower() for o in self.options}) != len(self.options):
            raise ValueError("Option texts must be distinct.")
        return self


class QuestionUpdate(BaseModel):
    """Partial update. options, when given, replace all options (pass back ids to keep them)."""

    type: QuestionType | None = None
    stem: str | None = None
    options: list[OptionIn] | None = None
    explanation: str | None = None
    deck_id: int | None = None
    reset_progress: bool = False


class CardUpdate(BaseModel):
    note: str | None = Field(default=None, max_length=10000, description="Private note; empty string deletes it.")
    suspended: bool | None = None


class SessionCreate(BaseModel):
    size: int | None = Field(default=None, ge=1, le=500)


class SessionUpdate(BaseModel):
    summary: str = Field(
        max_length=10000,
        description="The whole running summary (replaces the previous one): the user's reasoning, how the session is "
        "going and anything to remember next session. Empty string clears it.",
    )


class AnswerIn(BaseModel):
    question_id: int
    selected: list[str] = Field(min_length=1, max_length=5, description="Option ids the user picked.")
    confidence: Confidence


class SettingsUpdate(BaseModel):
    timezone: str | None = None
    desired_retention: float | None = Field(default=None, ge=0.7, le=0.99)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str | None) -> str | None:
        if value is not None and value not in available_timezones():
            raise ValueError("Unknown IANA timezone, e.g. America/New_York.")
        return value
