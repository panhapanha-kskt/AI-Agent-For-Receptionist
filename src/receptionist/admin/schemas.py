from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class QuestionOut(ORM):
    id: int
    question: str
    lang: str
    suggested_skill: str | None
    times_asked: int
    last_asked_at: datetime
    source: str = "agent"  # "agent" (couldn't answer) | "feedback" (caller rated 👎)
    previous_answer: str | None = None
    draft_answer: str | None = None


class ApproveIn(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)
    skill: str = Field(min_length=1, max_length=64)
    # Optional cleaned-up wording of the question to store.
    question: str | None = Field(default=None, max_length=1000)


class KnowledgeOut(ORM):
    id: int
    skill: str
    question: str
    answer: str
    lang: str
    version: int


class KnowledgeUpdateIn(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)


class MessageOut(ORM):
    id: int
    created_at: datetime
    caller_name: str
    contact: str
    for_office: str
    body: str
    status: str


class SkillsOut(BaseModel):
    skills: list[str]
    errors: list[str]


class FeedbackOut(ORM):
    id: int
    created_at: datetime
    turn_id: int
    rating: str
    skill: str | None
    question: str
    answer: str
    comment: str | None


class RoutingExampleOut(ORM):
    id: int
    created_at: datetime
    skill: str
    text: str


class ExportOut(BaseModel):
    skill: str
    file: str
