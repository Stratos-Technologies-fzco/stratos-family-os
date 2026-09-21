"""Domain model base class."""

from pydantic import BaseModel, ConfigDict


class StratosModel(BaseModel):
    """Base for domain models: strict about unknown fields, immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


__all__ = ["StratosModel"]
