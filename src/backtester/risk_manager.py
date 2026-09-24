"""風險控制與停損停利管理器 (Risk & Stop Loss Management Module).

實作 Mark Minervini 的 SEPA 風控原則：
1. 初始硬停損 (Initial Hard Stop Loss)：預設買進價之 -7%
2. 保本停損 (Break-Even Stop)：獲利達 +8% 時，將停損價上調至成本價 (保本)
3. 移動停利 (Trailing Stop)：獲利達 +15% 後，鎖定獲利，最高點回檔 6% 或跌破 20MA 平倉
4. 時間停損 (Time Stop)：進場 20 個交易日未發動且處於微幅虧損時平倉換股
"""

import logging
from typing import Optional, Tuple

from src.backtester.order import Position

logger = logging.getLogger(__name__)


class RiskManager:
    """風控與停損停利管理類別 (Risk Manager)."""

    def __init__(
        self,
        stop_loss_pct: float = 0.07,         # 初始硬停損 -7%
        break_even_pct: float = 0.08,        # 保本停損觸發門檻 +8%
        trailing_start_pct: float = 0.15,    # 移動停利啟動門檻 +15%
        trailing_drawdown_pct: float = 0.06, # 最高點回檔 6% 停利
        max_holding_days: int = 25,          # 最大無動靜持股天數 (時間停損)
    ) -> None:
        """初始化風控參數."""
        self.stop_loss_pct = float(stop_loss_pct)
        self.break_even_pct = float(break_even_pct)
        self.trailing_start_pct = float(trailing_start_pct)
        self.trailing_drawdown_pct = float(trailing_drawdown_pct)
        self.max_holding_days = int(max_holding_days)

    def calculate_initial_stop(self, entry_price: float, contraction_low: Optional[float] = None) -> float:
        """計算初始停損價格.

        以買進價格之 -stop_loss_pct (例如 -7%) 為主，
        若有最後一段收縮低點且距離更近，亦可做為參考。
        """
        hard_stop = entry_price * (1.0 - self.stop_loss_pct)
        if contraction_low and contraction_low > hard_stop and contraction_low < entry_price:
            return round(contraction_low, 2)
        return round(hard_stop, 2)

    def update_position_stop(self, pos: Position) -> None:
        """根據持股最新價格與獲利幅度動態上調停損點 (Progressive Stop Loss)."""
        current_gain = (pos.highest_price - pos.entry_price) / pos.entry_price

        # 1. 檢查是否觸發「保本停損 (Break-Even Stop)」
        if not pos.break_even_triggered and current_gain >= self.break_even_pct:
            # 移至成本價略上方 (cover fees)
            new_stop = pos.entry_price * 1.002
            if new_stop > pos.stop_loss_price:
                pos.stop_loss_price = round(new_stop, 2)
                pos.break_even_triggered = True
                logger.debug("[%s] %s 獲利達 +%.1f%%，停損調升至保本價 %.2f", pos.entry_date, pos.stock_id, current_gain * 100, new_stop)

        # 2. 檢查是否啟動「移動停利 (Trailing Stop)」
        if current_gain >= self.trailing_start_pct:
            trailing_stop = pos.highest_price * (1.0 - self.trailing_drawdown_pct)
            if trailing_stop > pos.stop_loss_price:
                pos.stop_loss_price = round(trailing_stop, 2)
                logger.debug("[%s] %s 獲利達 +%.1f%%，移動停利調升至 %.2f", pos.entry_date, pos.stock_id, current_gain * 100, trailing_stop)

    def check_exit(
        self,
        pos: Position,
        today_open: float,
        today_high: float,
        today_low: float,
        today_close: float,
        holding_days: int,
        sma20: Optional[float] = None,
    ) -> Tuple[bool, float, str]:
        """檢測當日是否觸發任何出場條件 (Check Exit Conditions).

        Returns:
            Tuple[bool, float, str]: (是否出場, 出場價格, 出場原因)
        """
        # 更新動態停損
        self.update_position_stop(pos)

        # 1. 檢查開盤跳空跌破停損 (Gap Down Stop Loss)
        if today_open <= pos.stop_loss_price:
            reason = "BREAK_EVEN_STOP" if pos.break_even_triggered else "INITIAL_STOP_LOSS"
            return True, today_open, reason

        # 2. 檢查盤中跌破停損價 (Intraday Stop Loss Hit)
        if today_low <= pos.stop_loss_price:
            reason = "BREAK_EVEN_STOP" if pos.break_even_triggered else "INITIAL_STOP_LOSS"
            return True, pos.stop_loss_price, reason

        # 3. 移動停利跌破 20MA (當獲利已超過 15% 且收盤跌破 20MA)
        unrealized_gain = (today_close - pos.entry_price) / pos.entry_price
        if unrealized_gain >= self.trailing_start_pct and sma20 and today_close < sma20:
            return True, today_close, "TRAILING_MA20_CROSS"

        # 4. 時間停損 (Time Stop): 持有超過 max_holding_days 天且虧損或無動靜
        if holding_days >= self.max_holding_days and unrealized_gain < 0.02:
            return True, today_close, "TIME_STOP_INACTIVE"

        return False, 0.0, ""
