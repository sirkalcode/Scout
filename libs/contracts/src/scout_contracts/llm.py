"""Wire models for llm-gateway's own endpoints."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

GatewayMode = Literal["fake", "real"]


class SpendToday(BaseModel):
    date: date
    mode: GatewayMode
    limit_usd: Decimal
    spent_usd: Decimal
    remaining_usd: Decimal
    requests: int
