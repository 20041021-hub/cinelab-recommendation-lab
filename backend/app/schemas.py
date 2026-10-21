from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import Path
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Input):
    login_name: StrictStr = Field(min_length=3, max_length=64, pattern=r"^[\w.-]+$")
    password: StrictStr = Field(min_length=12, max_length=128)


class Page(Input):
    page: int = Field(default=1, ge=1, le=100000)
    per_page: int = Field(default=20, ge=1, le=100)


class MovieQuery(Page):
    q: str | None = Field(default=None, max_length=100)
    genre: str | None = Field(default=None, max_length=40)


class RatingInput(Input):
    rating: Decimal

    @field_validator("rating", mode="before")
    @classmethod
    def exact_half_star(cls, value):
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            raise ValueError("评分必须是十进制数")
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            raise ValueError("评分无效") from None
        if not number.is_finite() or not Decimal("0.5") <= number <= Decimal("5.0") or number * 2 != (number * 2).to_integral_value():
            raise ValueError("评分范围为0.5到5.0，步长0.5")
        return number


IdPath = Annotated[str, Path(pattern=r"^[1-9][0-9]{0,19}$")]


def identifier(value: str) -> int:
    from .errors import APIError
    result = int(value)
    if result > 2**64 - 1:
        raise APIError(422, "INVALID_ID", "ID超出范围")
    return result
