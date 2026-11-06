"""DTO ключевых слов."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from corag_api.schemas.common import NonEmptyStr


class KeywordCreate(BaseModel):
    value: Annotated[NonEmptyStr, Field(max_length=100)]


class KeywordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    value: str
