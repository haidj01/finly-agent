from typing import Literal, Optional
from pydantic import BaseModel, Field

StrategyType = Literal["stop_loss", "take_profit", "price_target"]
QtyType      = Literal["shares", "all"]
Direction    = Literal["above", "below"]


class StrategyAction(BaseModel):
    side:     Literal["buy", "sell"]
    qty:      Optional[int] = Field(None, gt=0, le=10000)
    qty_type: QtyType = "shares"


class CreateStrategyRequest(BaseModel):
    name:      str           = Field(..., min_length=1, max_length=50)
    symbol:    str           = Field(..., min_length=1, max_length=10)
    type:      StrategyType
    condition: dict
    action:    StrategyAction
    enabled:   bool = True


class StrategyRow(BaseModel):
    id:         str
    name:       str
    symbol:     str
    type:       str
    condition:  dict
    action:     dict
    enabled:    bool
    created_at: str
