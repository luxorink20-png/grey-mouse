from dataclasses import dataclass
from typing import Optional


@dataclass
class TickRecord:
    timestamp_ms: int
    price: float
    size: float
    delta: float

    bid_volume: float
    ask_volume: float

    symbol: str

    rec_timestamp: float