"""投資組合與資金管理模組 (Portfolio & Capital Management Module).

負責模擬台股交易帳戶資金變動、部位管理、手續費折讓與證券交易稅扣除，
並記錄每日資產淨值歷史序列 (Daily Equity Curve)。
"""

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from src.backtester.order import Position, Trade

logger = logging.getLogger(__name__)

# 台股標準手續費率 0.1425% (最低 20 元) 與 證券交易稅率 0.3%
TW_FEE_RATE = 0.001425
TW_TAX_RATE = 0.003
MIN_FEE_NTD = 20.0


class Portfolio:
    """投資組合帳戶管理類別 (Portfolio Manager)."""

    def __init__(
        self,
        initial_capital: float = 1000000.0,
        fee_discount: float = 0.5,  # 券商手續費 5 折
        max_positions: int = 5,
    ) -> None:
        """初始化投資組合帳戶.

        Args:
            initial_capital: 初始本金 (預設 NT$ 1,000,000)
            fee_discount: 券商手續費折讓倍數 (預設 0.5 即 5 折)
            max_positions: 同時最多持股檔數 (預設 5 檔)
        """
        self.initial_capital = float(initial_capital)
        self.cash = float(initial_capital)
        self.fee_discount = float(fee_discount)
        self.max_positions = int(max_positions)

        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.daily_equity_history: List[Dict[str, Any]] = []

    @property
    def total_market_value(self) -> float:
        """所有持股市值加總."""
        return sum(pos.market_value for pos in self.positions.values())

    @property
    def total_equity(self) -> float:
        """當前總資產淨值 (現金 + 持股市值)."""
        return self.cash + self.total_market_value

    @property
    def available_slots(self) -> int:
        """剩餘可用持股名額."""
        return max(0, self.max_positions - len(self.positions))

    @property
    def target_position_size(self) -> float:
        """單檔目標分配資金 (總資產 / 最大持股數)."""
        if self.max_positions <= 0:
            return 0.0
        return self.total_equity / self.max_positions

    def calculate_buy_fee(self, amount: float) -> float:
        """計算買進手續費 (含折讓與最低手續費限制)."""
        raw_fee = amount * TW_FEE_RATE * self.fee_discount
        return max(MIN_FEE_NTD, raw_fee)

    def calculate_sell_fee_and_tax(self, amount: float) -> tuple[float, float]:
        """計算賣出手續費與證券交易稅."""
        raw_fee = amount * TW_FEE_RATE * self.fee_discount
        fee = max(MIN_FEE_NTD, raw_fee)
        tax = amount * TW_TAX_RATE
        return fee, tax

    def can_buy(self, stock_id: str, price: float) -> tuple[bool, int]:
        """檢查是否具備足夠資金與部位額度買進個股.

        Returns:
            tuple[bool, int]: (是否可買進, 可買進股數)
        """
        if stock_id in self.positions:
            return False, 0

        if self.available_slots <= 0:
            return False, 0

        if price <= 0:
            return False, 0

        # 計算單一標的預計投入金額 (不超過現金餘額的 98%，保留手續費餘裕)
        alloc_capital = min(self.target_position_size, self.cash * 0.98)
        if alloc_capital < price * 100:  # 至少需能買進 100 股
            return False, 0

        # 台股以整張 (1,000 股) 為優先計算，不足一張則以整百股零股為單位
        raw_shares = int(alloc_capital / price)
        if raw_shares >= 1000:
            shares = (raw_shares // 1000) * 1000  # 幾張
        elif raw_shares >= 100:
            shares = (raw_shares // 100) * 100   # 幾百股零股
        else:
            shares = raw_shares

        if shares <= 0:
            return False, 0

        total_cost = shares * price + self.calculate_buy_fee(shares * price)
        if total_cost > self.cash:
            return False, 0

        return True, shares

    def execute_buy(
        self,
        stock_id: str,
        name: str,
        date: str,
        price: float,
        shares: int,
        stop_loss_price: float,
        pivot_price: float = 0.0,
    ) -> Optional[Position]:
        """執行買進委託並扣除現金 (Execute BUY order)."""
        trade_amount = shares * price
        fee = self.calculate_buy_fee(trade_amount)
        total_cost = trade_amount + fee

        if total_cost > self.cash:
            logger.debug("資金不足，無法執行買進 %s %s (需 %f, 現有 %f)", stock_id, name, total_cost, self.cash)
            return None

        self.cash -= total_cost

        position = Position(
            stock_id=stock_id,
            name=name,
            entry_date=date,
            entry_price=price,
            shares=shares,
            current_price=price,
            highest_price=price,
            stop_loss_price=stop_loss_price,
            pivot_price=pivot_price,
        )
        self.positions[stock_id] = position

        logger.debug(
            "[%s] 買進 %s %s | 價格: %.2f | 股數: %d | 停損: %.2f | 費用: %.0f | 剩餘現金: %.0f",
            date, stock_id, name, price, shares, stop_loss_price, fee, self.cash,
        )
        return position

    def execute_sell(
        self,
        stock_id: str,
        date: str,
        price: float,
        reason: str,
        holding_days: int = 0,
    ) -> Optional[Trade]:
        """執行平倉賣出並結算損益 (Execute SELL order)."""
        if stock_id not in self.positions:
            return None

        pos = self.positions.pop(stock_id)
        gross_proceeds = pos.shares * price
        fee, tax = self.calculate_sell_fee_and_tax(gross_proceeds)
        net_proceeds = gross_proceeds - fee - tax

        self.cash += net_proceeds

        entry_fee = self.calculate_buy_fee(pos.cost_basis)
        gross_pnl = gross_proceeds - pos.cost_basis
        net_pnl = gross_pnl - entry_fee - fee - tax
        return_pct = (net_pnl / (pos.cost_basis + entry_fee)) * 100.0 if pos.cost_basis > 0 else 0.0

        trade = Trade(
            stock_id=stock_id,
            name=pos.name,
            entry_date=pos.entry_date,
            exit_date=date,
            entry_price=pos.entry_price,
            exit_price=price,
            shares=pos.shares,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            return_pct=return_pct,
            holding_days=holding_days,
            exit_reason=reason,
            entry_fee=entry_fee,
            exit_fee=fee,
            tax=tax,
        )
        self.trades.append(trade)

        logger.debug(
            "[%s] 賣出 %s %s (%s) | 成本: %.2f -> 賣價: %.2f | 損益: %+.0f (%.2f%%) | 天數: %d",
            date, stock_id, pos.name, reason, pos.entry_price, price, net_pnl, return_pct, holding_days,
        )
        return trade

    def record_daily_equity(
        self,
        date: str,
        price_map: Dict[str, float],
        benchmark_price: Optional[float] = None,
    ) -> None:
        """更新當日持股現價並記錄每日資產淨值 (Record Daily Equity)."""
        # 更新持股市值
        for stock_id, pos in self.positions.items():
            if stock_id in price_map and price_map[stock_id] > 0:
                pos.update_price(price_map[stock_id])

        record = {
            "date": date,
            "cash": round(self.cash, 2),
            "market_value": round(self.total_market_value, 2),
            "total_equity": round(self.total_equity, 2),
            "num_positions": len(self.positions),
            "benchmark_price": benchmark_price,
        }
        self.daily_equity_history.append(record)

    def get_equity_dataframe(self) -> pd.DataFrame:
        """轉換每日淨值歷史為 Pandas DataFrame."""
        if not self.daily_equity_history:
            return pd.DataFrame()
        df = pd.DataFrame(self.daily_equity_history)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df

    def get_trades_dataframe(self) -> pd.DataFrame:
        """轉換所有平倉交易紀錄為 Pandas DataFrame."""
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame([t.to_dict() for t in self.trades])
