"""DTO авторов."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from corag_api.schemas.common import NonEmptyStr, PatchModel

ORCID_PATTERN = r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$"
Orcid = Annotated[str, Field(pattern=ORCID_PATTERN, examples=["0000-0002-1825-0097"])]
FullName = Annotated[NonEmptyStr, Field(max_length=255)]
Affiliation = Annotated[NonEmptyStr, Field(max_length=500)]


class AuthorCreate(BaseModel):
    full_name: FullName
    affiliation: Affiliation | None = None
    orcid: Orcid | None = None


class AuthorUpdate(PatchModel):
    nullable = frozenset({"affiliation", "orcid"})

    full_name: FullName | None = None
    affiliation: Affiliation | None = None
    orcid: Orcid | None = None


class AuthorShort(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str


class AuthorOut(AuthorShort):
    affiliation: str | None
    orcid: str | None
