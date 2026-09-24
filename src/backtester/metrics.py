"""量化績效評估指標計算模組 (Performance Metrics Calculation Module).

計算總報酬率、年化複合成長率 (CAGR)、最大回撤 (MDD)、勝率、賺賠比、獲利因子、
夏普比率 (Sharpe Ratio)、卡瑪比率 (Calmar Ratio) 以及大盤 (^TWII) Alpha 超額報酬對比。
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from src.backtester.order import Trade


@dataclass
class PerformanceMetrics:
    """策略回測績效指標數據模型."""

    initial_capital: float
    final_equity: float
    total_net_profit: float
    total_return_pct: float
    cagr_pct: float
    total_trading_days: int

    # 交易統計
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    payoff_ratio: float
    profit_factor: float
    avg_trade_return_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    max_win_pct: float
    max_loss_pct: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    avg_holding_days: float

    # 風險與波動指標
    max_drawdown_pct: float
    mdd_peak_date: str
    mdd_trough_date: str
    sharpe_ratio: float
    calmar_ratio: float

    # 大盤對比 (Benchmark ^TWII)
    benchmark_return_pct: Optional[float] = None
    benchmark_cagr_pct: Optional[float] = None
    alpha_pct: Optional[float] = None


def calculate_performance_metrics(
    equity_df: pd.DataFrame,
    trades: List[Trade],
    initial_capital: float,
    risk_free_rate: float = 0.015,  # 台灣無風險利率預設 1.5%
) -> PerformanceMetrics:
    """計算完整的回測績效指標."""
    if equity_df.empty:
        raise ValueError("Equity DataFrame 為空，無法計算績效指標")

    equity_df = equity_df.copy()
    final_equity = float(equity_df["total_equity"].iloc[-1])
    total_net_profit = final_equity - initial_capital
    total_return_pct = (total_net_profit / initial_capital) * 100.0

    total_days = len(equity_df)
    years = total_days / 252.0 if total_days > 0 else 1.0

    # 年化複合成長率 CAGR
    if final_equity > 0 and years > 0:
        cagr_pct = ((final_equity / initial_capital) ** (1.0 / years) - 1.0) * 100.0
    else:
        cagr_pct = -100.0

    # 每日報酬率與最大回撤 MDD
    equity_df["daily_return"] = equity_df["total_equity"].pct_change().fillna(0.0)
    equity_df["peak"] = equity_df["total_equity"].cummax()
    equity_df["drawdown"] = (equity_df["total_equity"] - equity_df["peak"]) / equity_df["peak"]

    max_drawdown = float(equity_df["drawdown"].min())
    max_drawdown_pct = abs(max_drawdown) * 100.0

    # 找出 MDD 發生日期
    trough_idx = equity_df["drawdown"].idxmin()
    trough_date = str(equity_df["date"].iloc[trough_idx])[:10] if pd.notna(trough_idx) else ""
    peak_idx = equity_df.loc[:trough_idx, "total_equity"].idxmax() if pd.notna(trough_idx) else 0
    peak_date = str(equity_df["date"].iloc[peak_idx])[:10] if pd.notna(peak_idx) else ""

    # 夏普比率 Sharpe Ratio (年化)
    daily_returns = equity_df["daily_return"].values
    mean_daily = np.mean(daily_returns)
    std_daily = np.std(daily_returns, ddof=1) if len(daily_returns) > 1 else 0.0
    daily_rf = (1.0 + risk_free_rate) ** (1.0 / 252.0) - 1.0

    if std_daily > 0:
        sharpe_ratio = float((mean_daily - daily_rf) / std_daily * np.sqrt(252))
    else:
        sharpe_ratio = 0.0

    # 卡瑪比率 Calmar Ratio
    calmar_ratio = float(cagr_pct / max_drawdown_pct) if max_drawdown_pct > 0 else 0.0

    # 交易統計指標
    total_trades = len(trades)
    if total_trades > 0:
        wins = [t for t in trades if t.net_pnl > 0]
        losses = [t for t in trades if t.net_pnl <= 0]

        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate_pct = (winning_trades / total_trades) * 100.0

        total_gain = sum(t.net_pnl for t in wins)
        total_loss = abs(sum(t.net_pnl for t in losses))

        profit_factor = float(total_gain / total_loss) if total_loss > 0 else 999.0

        avg_win_amt = total_gain / winning_trades if winning_trades > 0 else 0.0
        avg_loss_amt = total_loss / losing_trades if losing_trades > 0 else 0.0
        payoff_ratio = float(avg_win_amt / avg_loss_amt) if avg_loss_amt > 0 else 999.0

        trade_returns = [t.return_pct for t in trades]
        avg_trade_return_pct = float(np.mean(trade_returns))
        avg_win_pct = float(np.mean([t.return_pct for t in wins])) if wins else 0.0
        avg_loss_pct = float(np.mean([t.return_pct for t in losses])) if losses else 0.0
        max_win_pct = float(max(trade_returns))
        max_loss_pct = float(min(trade_returns))

        avg_holding_days = float(np.mean([t.holding_days for t in trades]))

        # 連勝與連敗統計
        max_cons_wins, max_cons_losses = 0, 0
        cur_wins, cur_losses = 0, 0
        for t in trades:
            if t.net_pnl > 0:
                cur_wins += 1
                cur_losses = 0
                max_cons_wins = max(max_cons_wins, cur_wins)
            else:
                cur_losses += 1
                cur_wins = 0
                max_cons_losses = max(max_cons_losses, cur_losses)
    else:
        winning_trades = losing_trades = 0
        win_rate_pct = payoff_ratio = profit_factor = 0.0
        avg_trade_return_pct = avg_win_pct = avg_loss_pct = max_win_pct = max_loss_pct = 0.0
        max_cons_wins = max_cons_losses = 0
        avg_holding_days = 0.0

    # 大盤基準比較
    benchmark_return_pct = None
    benchmark_cagr_pct = None
    alpha_pct = None

    if "benchmark_price" in equity_df.columns and equity_df["benchmark_price"].dropna().count() >= 2:
        valid_bench = equity_df.dropna(subset=["benchmark_price"])
        bench_start = float(valid_bench["benchmark_price"].iloc[0])
        bench_end = float(valid_bench["benchmark_price"].iloc[-1])
        if bench_start > 0:
            benchmark_return_pct = float((bench_end - bench_start) / bench_start * 100.0)
            if years > 0 and bench_end > 0:
                benchmark_cagr_pct = float(((bench_end / bench_start) ** (1.0 / years) - 1.0) * 100.0)
                alpha_pct = float(cagr_pct - benchmark_cagr_pct)

    return PerformanceMetrics(
        initial_capital=initial_capital,
        final_equity=round(final_equity, 2),
        total_net_profit=round(total_net_profit, 2),
        total_return_pct=round(total_return_pct, 2),
        cagr_pct=round(cagr_pct, 2),
        total_trading_days=total_days,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate_pct=round(win_rate_pct, 2),
        payoff_ratio=round(payoff_ratio, 2),
        profit_factor=round(profit_factor, 2),
        avg_trade_return_pct=round(avg_trade_return_pct, 2),
        avg_win_pct=round(avg_win_pct, 2),
        avg_loss_pct=round(avg_loss_pct, 2),
        max_win_pct=round(max_win_pct, 2),
        max_loss_pct=round(max_loss_pct, 2),
        max_consecutive_wins=max_cons_wins,
        max_consecutive_losses=max_cons_losses,
        avg_holding_days=round(avg_holding_days, 1),
        max_drawdown_pct=round(max_drawdown_pct, 2),
        mdd_peak_date=peak_date,
        mdd_trough_date=trough_date,
        sharpe_ratio=round(sharpe_ratio, 2),
        calmar_ratio=round(calmar_ratio, 2),
        benchmark_return_pct=round(benchmark_return_pct, 2) if benchmark_return_pct is not None else None,
        benchmark_cagr_pct=round(benchmark_cagr_pct, 2) if benchmark_cagr_pct is not None else None,
        alpha_pct=round(alpha_pct, 2) if alpha_pct is not None else None,
    )
