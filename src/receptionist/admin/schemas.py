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
