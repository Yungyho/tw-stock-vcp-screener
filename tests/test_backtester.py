"""回測系統單元測試 (Unit Tests for Backtesting Engine)."""

import pandas as pd
import pytest

from src.backtester.metrics import calculate_performance_metrics
from src.backtester.order import Position, Trade
from src.backtester.portfolio import Portfolio
from src.backtester.risk_manager import RiskManager


def test_portfolio_buy_and_sell():
    """測試投資組合買進、賣出、手續費與損益計算."""
    p = Portfolio(initial_capital=1000000.0, fee_discount=0.5, max_positions=5)
    assert p.total_equity == 1000000.0
    assert p.available_slots == 5

    # 1. 測試買進台積電 (2330) 1000 股 @ 800 元
    can_buy, shares = p.can_buy("2330", 800.0)
    assert can_buy is True
    assert shares > 0

    pos = p.execute_buy(
        stock_id="2330",
        name="台積電",
        date="2024-01-02",
        price=800.0,
        shares=1000,
        stop_loss_price=744.0,  # -7%
    )
    assert pos is not None
    assert "2330" in p.positions
    assert p.available_slots == 4

    # 買進費用: 800,000 * 0.001425 * 0.5 = 570
    assert p.cash == pytest.approx(1000000.0 - 800000.0 - 570.0, abs=1.0)
    assert p.total_equity == pytest.approx(1000000.0 - 570.0, abs=1.0)

    # 2. 測試獲利平倉賣出 @ 880 元 (+10%)
    trade = p.execute_sell(
        stock_id="2330",
        date="2024-01-15",
        price=880.0,
        reason="TAKE_PROFIT",
        holding_days=10,
    )
    assert trade is not None
    assert trade.gross_pnl == 80000.0
    assert trade.net_pnl > 70000.0
    assert trade.return_pct > 8.0
    assert p.available_slots == 5
    assert len(p.trades) == 1
    assert p.total_equity > 1000000.0


def test_risk_manager_stops():
    """測試風控管理器之初始停損、保本停損與移動停利."""
    rm = RiskManager(
        stop_loss_pct=0.07,
        break_even_pct=0.08,
        trailing_start_pct=0.15,
        trailing_drawdown_pct=0.06,
        max_holding_days=20,
    )

    # 1. 初始停損計算
    init_stop = rm.calculate_initial_stop(100.0)
    assert init_stop == 93.0  # 100 * 0.93

    pos = Position(
        stock_id="2454",
        name="聯發科",
        entry_date="2024-01-02",
        entry_price=100.0,
        shares=1000,
        current_price=100.0,
        highest_price=100.0,
        stop_loss_price=93.0,
    )

    # 2. 正常波動無觸發
    should_exit, _, _ = rm.check_exit(pos, today_open=102.0, today_high=103.0, today_low=98.0, today_close=101.0, holding_days=1)
    assert should_exit is False

    # 3. 股價漲至 110 (+10% > 8%) -> 應觸發保本停損 (Break-Even)
    pos.update_price(110.0)
    rm.update_position_stop(pos)
    assert pos.break_even_triggered is True
    assert pos.stop_loss_price >= 100.0

    # 4. 股價大漲至 130 (+30% > 15%) -> 應啟動移動停利 (最高點回檔 6% = 130 * 0.94 = 122.2)
    pos.update_price(130.0)
    rm.update_position_stop(pos)
    assert pos.stop_loss_price == pytest.approx(130.0 * 0.94, abs=0.5)

    # 5. 當日回跌碰觸移動停利價 (Low = 120 <= 122.2) -> 觸發停利出場
    should_exit, exit_price, reason = rm.check_exit(pos, today_open=125.0, today_high=126.0, today_low=120.0, today_close=121.0, holding_days=8)
    assert should_exit is True
    assert "STOP" in reason


def test_performance_metrics_calculation():
    """測試量化績效指標計算 (CAGR, MDD, Sharpe, Win Rate)."""
    equity_data = {
        "date": pd.date_range("2023-01-01", periods=252, freq="B"),
        "total_equity": [1000000.0 * (1.0 + 0.001 * i) for i in range(252)],
        "benchmark_price": [15000.0 * (1.0 + 0.0005 * i) for i in range(252)],
    }
    df = pd.DataFrame(equity_data)

    trades = [
        Trade("2330", "台積電", "2023-01-05", "2023-01-20", 500, 550, 1000, 50000, 48000, 9.6, 11, "PROFIT"),
        Trade("2454", "聯發科", "2023-02-01", "2023-02-10", 700, 660, 500, -20000, -21500, -6.1, 7, "STOP_LOSS"),
    ]

    metrics = calculate_performance_metrics(df, trades, initial_capital=1000000.0)
    assert metrics.total_trades == 2
    assert metrics.winning_trades == 1
    assert metrics.losing_trades == 1
    assert metrics.win_rate_pct == 50.0
    assert metrics.total_return_pct > 20.0
    assert metrics.cagr_pct > 20.0
    assert metrics.max_drawdown_pct >= 0.0
    assert metrics.profit_factor > 1.0
