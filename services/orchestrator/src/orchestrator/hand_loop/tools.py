"""A local tool: a Pydantic model for its input, a description, and an async function."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, cast

from anthropic import transform_schema
from anthropic.types import ToolParam
from pydantic import BaseModel


@dataclass(frozen=True)
class LocalTool:
    name: str
    description: str
    input_model: type[BaseModel]
    func: Callable[..., Coroutine[Any, Any, str]]

    def input_schema(self) -> dict[str, Any]:
        """The schema Claude sees.

        Strict mode does not support every JSON Schema rule, so the SDK moves rules such as
        `pattern` into the description. The Pydantic model still enforces them.
        """
        return transform_schema(self.input_model)

    def definition(self) -> ToolParam:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": cast(Any, self.input_schema()),
            "strict": True,
        }

    async def call(self, raw_input: object) -> str:
        """Validates the input, then runs the tool. Raises pydantic.ValidationError on bad input."""
        values = self.input_model.model_validate(raw_input)
        return await self.func(**values.model_dump())
