# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field
from typing import Literal

class AISignal(BaseModel):
    """Модель для валидации торгового сигнала от AI."""
    signal: Literal['LONG', 'SHORT', 'WAIT'] = Field(..., description="Сигнал: LONG, SHORT или WAIT")
    confidence: Literal['HIGH', 'MEDIUM', 'LOW'] = Field(..., description="Уверенность в сигнале")
    justification: str = Field(..., description="Краткое обоснование")

class TradeOrder(BaseModel):
    """Модель, описывающая полный торговый приказ."""
    signal: Literal['LONG', 'SHORT']
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: Literal['HIGH', 'MEDIUM', 'LOW']
    justification: str
