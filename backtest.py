"""VCP Stage 2 策略歷史回測主程式 (Main Backtesting CLI Entry Point).

提供完整的歷史回測執行、參數設定、績效報表格式化輸出與圖表繪製功能。
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from config.settings import get_settings
from src.backtester.engine import BacktestEngine
from src.data_fetcher import DataFetcher
from src.db.manager import DBManager
from src.reporter.chart_plotter import plot_equity_curve
from src.stock_list import fetch_stock_list


def setup_logging():
    """設定日誌系統."""
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "backtest.log"

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(str(log_file), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def print_backtest_report(metrics, initial_capital: float, start_date: str, end_date: str, trades: list) -> None:
    """輸出格式化的量化回測績效報告."""
    sep = "=" * 70
    sub_sep = "-" * 70

    print(f"\n{sep}")
    print(f"             📊 台股 VCP Stage 2 策略歷史回測績效報告             ")
    print(f"{sep}")
    print(f" 📅 回測區間: {start_date} ~ {end_date} (共 {metrics.total_trading_days} 個交易日)")
    print(f" 💰 初始本金: NT$ {initial_capital:,.0f}  ->  期末總值: NT$ {metrics.final_equity:,.0f}")
    print(f" 💵 累計淨利: NT$ {metrics.total_net_profit:+,.0f}")
    print(f"{sub_sep}")

    print(" 📈 【報酬率與大盤對比】")
    print(f"   • 策略總報酬率 (Total Return)  : {metrics.total_return_pct:+8.2f} %")
    print(f"   • 年化複合成長率 (CAGR)         : {metrics.cagr_pct:+8.2f} %")
    if metrics.benchmark_return_pct is not None:
        print(f"   • 加權指數大盤報酬 (^TWII)     : {metrics.benchmark_return_pct:+8.2f} %")
        print(f"   • 大盤年化報酬 (Benchmark CAGR) : {metrics.benchmark_cagr_pct:+8.2f} %")
        print(f"   • 超額報酬 (Alpha)              : {metrics.alpha_pct:+8.2f} %")
    print(f"{sub_sep}")

    print(" 🎯 【交易勝率與賺賠比】")
    print(f"   • 總交易次數 (Total Trades)    : {metrics.total_trades:6d} 筆")
    print(f"   • 獲利交易筆數 (Winning Trades): {metrics.winning_trades:6d} 筆 (勝率: {metrics.win_rate_pct:.1f}%)")
    print(f"   • 虧損交易筆數 (Losing Trades) : {metrics.losing_trades:6d} 筆")
    print(f"   • 賺賠比 (Payoff Ratio)        : {metrics.payoff_ratio:8.2f} (平均獲利/平均虧損)")
    print(f"   • 獲利因子 (Profit Factor)     : {metrics.profit_factor:8.2f} (總獲利/總虧損)")
    print(f"   • 單筆平均報酬率               : {metrics.avg_trade_return_pct:+8.2f} %")
    print(f"   • 平均獲利幅度 (Avg Win)       : {metrics.avg_win_pct:+8.2f} %")
    print(f"   • 平均虧損幅度 (Avg Loss)      : {metrics.avg_loss_pct:+8.2f} %")
    print(f"   • 最大單筆獲利 / 虧損          : {metrics.max_win_pct:+.2f}% / {metrics.max_loss_pct:+.2f}%")
    print(f"   • 最長連續獲利 / 連續虧損      : {metrics.max_consecutive_wins} 次 / {metrics.max_consecutive_losses} 次")
    print(f"   • 平均持股天數                 : {metrics.avg_holding_days:.1f} 個交易日")
    print(f"{sub_sep}")

    print(" 🛡️ 【風險與回撤控制】")
    print(f"   • 最大回撤幅度 (Max Drawdown)  : -{metrics.max_drawdown_pct:7.2f} %")
    print(f"   • MDD 發生波段                 : {metrics.mdd_peak_date} -> {metrics.mdd_trough_date}")
    print(f"   • 夏普比率 (Sharpe Ratio)      : {metrics.sharpe_ratio:8.2f}")
    print(f"   • 卡瑪比率 (Calmar Ratio)      : {metrics.calmar_ratio:8.2f}")
    print(f"{sep}\n")

    # 顯示前 5 筆最佳獲利與最大虧損交易
    if trades:
        sorted_trades = sorted(trades, key=lambda t: t.net_pnl, reverse=True)
        print(" 🏆 【Top 3 獲利交易範例】")
        for idx, t in enumerate(sorted_trades[:3], 1):
            print(f"   {idx}. {t.stock_id} {t.name} ({t.entry_date} ~ {t.exit_date}) 獲利: NT$ {t.net_pnl:+,.0f} ({t.return_pct:+.1f}%) 持有 {t.holding_days} 天 [{t.exit_reason}]")

        print("\n ⚠️ 【Top 3 虧損交易範例】")
        for idx, t in enumerate(sorted_trades[-3:][::-1], 1):
            print(f"   {idx}. {t.stock_id} {t.name} ({t.entry_date} ~ {t.exit_date}) 損益: NT$ {t.net_pnl:+,.0f} ({t.return_pct:+.1f}%) 持有 {t.holding_days} 天 [{t.exit_reason}]")
        print(f"\n{sep}\n")


def main():
    parser = argparse.ArgumentParser(description="Taiwan Stock VCP Stage 2 Strategy Backtester (5-Year)")
    parser.add_argument("--start", type=str, default="2021-01-01", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="Backtest end date (YYYY-MM-DD, default today)")
    parser.add_argument("--capital", type=float, default=None, help="Initial capital in NTD (default 1,000,000)")
    parser.add_argument("--max-positions", type=int, default=None, help="Max concurrent positions (default 5)")
    parser.add_argument("--stop-loss", type=float, default=None, help="Initial stop loss ratio (default 0.07 for -7%%)")
    parser.add_argument("--market", choices=["all", "listed", "otc"], default="listed", help="Market filter (default listed)")
    parser.add_argument("--download", action="store_true", help="Download/update 5-year historical price data and ^TWII benchmark before backtesting")
    parser.add_argument("--no-plot", action="store_true", help="Skip generating equity curve chart")
    args = parser.parse_args()

    setup_logging()
    settings = get_settings()

    initial_capital = args.capital or settings.BACKTEST_CAPITAL
    max_positions = args.max_positions or settings.BACKTEST_MAX_POSITIONS
    stop_loss = args.stop_loss or settings.BACKTEST_STOP_LOSS
    market = args.market or settings.MARKET

    logging.info("啟動台股 VCP Stage 2 策略歷史回測系統...")
    logging.info("回測參數: 初始本金=NT$ %s | 最大持股=%d | 硬停損=-%.1f%% | 市場=%s",
                 f"{initial_capital:,.0f}", max_positions, stop_loss * 100, market)

    with DBManager(settings.DB_PATH) as db:
        fetcher = DataFetcher(db)

        # ── 檢查是否需要下載 5 年數據 ──
        if args.download:
            logging.info("正在更新全市場股票清單與 5 年歷史日 K 棒資料...")
            stock_list = fetch_stock_list()
            db.upsert_stock_list(stock_list)
            # 下載大盤基準 ^TWII
            fetcher.fetch_benchmark("^TWII", period="5y")
            # 下載個股 5 年數據
            fetcher.fetch_all(stock_list, period="5y")

        # ── 初始化回測引擎 ──
        engine = BacktestEngine(
            db_manager=db,
            initial_capital=initial_capital,
            max_positions=max_positions,
            stop_loss_pct=stop_loss,
            fee_discount=settings.BACKTEST_FEE_DISCOUNT,
            market=market,
            settings=settings,
        )

        # 載入數據
        engine.load_data(start_date=args.start, end_date=args.end)

        # 執行回測
        metrics = engine.run()

        # ── 輸出報表 ──
        start_date = engine.all_trading_dates[0] if engine.all_trading_dates else args.start
        end_date = engine.all_trading_dates[-1] if engine.all_trading_dates else "N/A"
        print_backtest_report(metrics, initial_capital, start_date, end_date, engine.portfolio.trades)

        # ── 導出交易紀錄 CSV ──
        trades_df = engine.portfolio.get_trades_dataframe()
        if not trades_df.empty:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = Path("logs") / f"backtest_trades_{timestamp}.csv"
            trades_df.to_csv(str(csv_path), index=False, encoding="utf-8-sig")
            logging.info("已將完整交易紀錄明細導出至: %s", csv_path.resolve())

        # ── 繪製資產淨值曲線圖 ──
        if not args.no_plot:
            equity_df = engine.portfolio.get_equity_dataframe()
            chart_path = plot_equity_curve(
                equity_df,
                output_path="logs/backtest_equity_curve.png",
                strategy_name="VCP Stage 2 Strategy",
            )
            if chart_path:
                logging.info("資產淨值走勢圖已儲存至: %s", chart_path)


if __name__ == "__main__":
    main()
