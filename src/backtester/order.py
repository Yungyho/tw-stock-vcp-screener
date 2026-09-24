"""訂單、持股部位與交易記錄資料模型 (Order, Position, and Trade Data Models)."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Order:
    """委託訂單模型 (Order Model)."""

    stock_id: str
    name: str
    action: str  # "BUY" 或 "SELL"
    order_date: str
    price: float
    shares: int
    reason: str = ""  # 進出場原因 (例如 "VCP_BREAKOUT", "STOP_LOSS", "TRAILING_STOP")


@dataclass
class Position:
    """持股部位模型 (Active Position Model)."""

    stock_id: str
    name: str
    entry_date: str
    entry_price: float
    shares: int
    current_price: float
    highest_price: float
    stop_loss_price: float
    break_even_triggered: bool = False
    pivot_price: float = 0.0

    @property
    def market_value(self) -> float:
        """當前部位市值."""
        return self.shares * self.current_price

    @property
    def cost_basis(self) -> float:
        """原始買進成本 (不含手續費)."""
        return self.shares * self.entry_price

    @property
    def unrealized_pnl(self) -> float:
        """未實現損益金額."""
        return self.market_value - self.cost_basis

    @property
    def unrealized_return_pct(self) -> float:
        """未實現報酬率 (%)."""
        if self.entry_price <= 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price * 100.0

    def update_price(self, new_price: float) -> None:
        """更新當前價格與歷史最高價."""
        self.current_price = new_price
        if new_price > self.highest_price:
            self.highest_price = new_price


@dataclass
class Trade:
    """已實現平倉交易紀錄模型 (Completed Trade Record)."""

    stock_id: str
    name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    gross_pnl: float
    net_pnl: float
    return_pct: float
    holding_days: int
    exit_reason: str
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    tax: float = 0.0

    def to_dict(self) -> dict:
        """轉為字典格式 (用於導出 CSV 與日誌記錄)."""
        return {
            "stock_id": self.stock_id,
            "name": self.name,
            "entry_date": self.entry_date,
            "exit_date": self.exit_date,
            "holding_days": self.holding_days,
            "entry_price": round(self.entry_price, 2),
            "exit_price": round(self.exit_price, 2),
            "shares": self.shares,
            "gross_pnl": round(self.gross_pnl, 0),
            "fee_total": round(self.entry_fee + self.exit_fee, 0),
            "tax": round(self.tax, 0),
            "net_pnl": round(self.net_pnl, 0),
            "return_pct": round(self.return_pct, 2),
            "exit_reason": self.exit_reason,
        }
