"""滾動時間軸回測引擎 (Rolling Point-in-Time Backtest Engine).

依序遍歷歷史交易日，使用無前視偏差 (Point-in-Time) 的歷史切片數據進行
Stage 2 + VCP 型態篩選、買進訊號判定、動態停損停利風控撮合與投資組合淨值結算。
"""

import logging
import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from config.settings import Settings, get_settings
from src.backtester.metrics import PerformanceMetrics, calculate_performance_metrics
from src.backtester.portfolio import Portfolio
from src.backtester.risk_manager import RiskManager
from src.beta import calculate_beta
from src.db.manager import DBManager
from src.scorer import calculate_score, rank_results
from src.trend_template import check_trend_template
from src.vcp_detector import detect_vcp

logger = logging.getLogger(__name__)


class BacktestEngine:
    """VCP Stage 2 策略歷史回測引擎."""

    def __init__(
        self,
        db_manager: DBManager,
        initial_capital: float = 1000000.0,
        max_positions: int = 5,
        stop_loss_pct: float = 0.07,
        fee_discount: float = 0.5,
        market: str = "listed",  # 'listed', 'otc', 'all'
        scan_interval_days: int = 1,  # 每日選股
        settings: Optional[Settings] = None,
    ) -> None:
        """初始化回測引擎."""
        self.db = db_manager
        self.settings = settings or get_settings()
        self.market_filter = market.strip().lower()
        self.scan_interval = max(1, scan_interval_days)

        self.portfolio = Portfolio(
            initial_capital=initial_capital,
            fee_discount=fee_discount,
            max_positions=max_positions,
        )
        self.risk_manager = RiskManager(
            stop_loss_pct=stop_loss_pct,
            break_even_pct=0.08,
            trailing_start_pct=0.15,
            trailing_drawdown_pct=0.06,
            max_holding_days=25,
        )

        self.stock_data_cache: Dict[str, pd.DataFrame] = {}
        self.stock_meta_cache: Dict[str, Dict[str, str]] = {}
        self.benchmark_df: pd.DataFrame = pd.DataFrame()
        self.benchmark_otc_df: pd.DataFrame = pd.DataFrame()
        self.all_trading_dates: List[str] = []

    def _prepare_df_for_analysis(self, df: pd.DataFrame) -> pd.DataFrame:
        """將資料庫欄位 (小寫) 標準化為大寫欄位."""
        col_map = {
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "adj_close": "Adj Close",
            "volume": "Volume",
        }
        return df.rename(columns=col_map)

    def load_data(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> None:
        """預先自資料庫載入所有標的歷史數據至記憶體，實現超高速回測."""
        logger.info("正在從資料庫載入回測所需歷史數據至記憶體...")
        t0 = time.time()

        all_stocks = self.db.get_all_stocks()
        if not all_stocks:
            raise RuntimeError("資料庫中無任何股票清單，請先執行資料下載")

        # 篩選市場別
        stocks_to_load = []
        for s in all_stocks:
            s_mkt = s.get("market", "").lower()
            if self.market_filter == "listed" and s_mkt != "listed":
                continue
            elif self.market_filter == "otc" and s_mkt != "otc":
                continue
            stocks_to_load.append(s)

        logger.info("符合市場條件 (%s) 之標的共 %d 檔，開始載入價格序列...", self.market_filter, len(stocks_to_load))

        trading_dates_set = set()

        for s in stocks_to_load:
            s_id = str(s["stock_id"])
            df = self.db.get_price_history(s_id, days=1800)
            if df.empty or len(df) < 252:
                continue

            df = self._prepare_df_for_analysis(df)
            df = df.sort_values("date").reset_index(drop=True)
            df.set_index("date", inplace=False)

            self.stock_data_cache[s_id] = df
            self.stock_meta_cache[s_id] = {
                "name": s.get("name", s_id),
                "market": s.get("market", ""),
                "market_cap": float(s.get("market_cap", 0.0)),
            }

            for d in df["date"].values:
                trading_dates_set.add(str(d)[:10])

        # 載入上市大盤基準數據 (TAIEX)
        bench_listed_symbol = self.settings.BENCHMARK_LISTED
        bench_df = self.db.get_price_history(bench_listed_symbol, days=1800)
        if not bench_df.empty:
            bench_df = self._prepare_df_for_analysis(bench_df)
            bench_df = bench_df.sort_values("date").reset_index(drop=True)
            self.benchmark_df = bench_df

        # 載入上櫃大盤基準數據 (TPEx)
        bench_otc_symbol = self.settings.BENCHMARK_OTC
        bench_otc_df = self.db.get_price_history(bench_otc_symbol, days=1800)
        if not bench_otc_df.empty:
            bench_otc_df = self._prepare_df_for_analysis(bench_otc_df)
            bench_otc_df = bench_otc_df.sort_values("date").reset_index(drop=True)
            self.benchmark_otc_df = bench_otc_df

        sorted_dates = sorted(list(trading_dates_set))
        if start_date:
            sorted_dates = [d for d in sorted_dates if d >= start_date]
        if end_date:
            sorted_dates = [d for d in sorted_dates if d <= end_date]

        self.all_trading_dates = sorted_dates
        t1 = time.time()
        logger.info(
            "歷史數據載入完成 (耗時 %.2f 秒): 共 %d 檔有效股票，涵蓋 %d 個回測交易日 (%s 至 %s)",
            t1 - t0,
            len(self.stock_data_cache),
            len(self.all_trading_dates),
            self.all_trading_dates[0] if self.all_trading_dates else "N/A",
            self.all_trading_dates[-1] if self.all_trading_dates else "N/A",
        )

    def run(self, progress_callback: Optional[callable] = None) -> PerformanceMetrics:
        """執行滾動時間序列回測."""
        if not self.all_trading_dates:
            raise RuntimeError("尚未載入回測交易日資料，請先呼叫 load_data()")

        total_days = len(self.all_trading_dates)
        logger.info("開始執行歷史回測 (共 %d 個交易日)...", total_days)

        # 預先建立大盤價格字典以加速查找
        bench_price_map = {}
        if not self.benchmark_df.empty:
            for _, row in self.benchmark_df.iterrows():
                bench_price_map[str(row["date"])[:10]] = float(row["Close"])

        # 逐日推進
        for day_idx, curr_date in enumerate(self.all_trading_dates):
            if day_idx % 50 == 0 or day_idx == total_days - 1:
                logger.info(
                    "回測進度: [%d/%d] 日期: %s | 當前淨值: %.0f | 持股數: %d | 累計平倉: %d 筆",
                    day_idx + 1,
                    total_days,
                    curr_date,
                    self.portfolio.total_equity,
                    len(self.portfolio.positions),
                    len(self.portfolio.trades),
                )
                if progress_callback:
                    progress_callback(day_idx + 1, total_days, curr_date, self.portfolio.total_equity)

            # ── 步驟 1: 檢查現有持股的平倉與停損停利 ──
            current_day_prices = {}
            active_stock_ids = list(self.portfolio.positions.keys())

            for s_id in active_stock_ids:
                pos = self.portfolio.positions[s_id]
                df = self.stock_data_cache.get(s_id)
                if df is None:
                    continue

                # 取得當日 K 棒
                day_bar = df[df["date"] == curr_date]
                if day_bar.empty:
                    continue

                bar = day_bar.iloc[0]
                t_open = float(bar["Open"])
                t_high = float(bar["High"])
                t_low = float(bar["Low"])
                t_close = float(bar["Close"])
                current_day_prices[s_id] = t_close

                # 計算 20MA
                idx_in_df = day_bar.index[0]
                sma20 = float(df["Close"].iloc[max(0, idx_in_df - 19) : idx_in_df + 1].mean()) if idx_in_df >= 19 else None

                # 計算持股交易日數
                entry_idx = df[df["date"] == pos.entry_date].index
                holding_days = int(idx_in_df - entry_idx[0]) if not entry_idx.empty else 1

                # 檢測風控出場
                should_exit, exit_price, exit_reason = self.risk_manager.check_exit(
                    pos=pos,
                    today_open=t_open,
                    today_high=t_high,
                    today_low=t_low,
                    today_close=t_close,
                    holding_days=holding_days,
                    sma20=sma20,
                )

                if should_exit:
                    self.portfolio.execute_sell(
                        stock_id=s_id,
                        date=curr_date,
                        price=exit_price,
                        reason=exit_reason,
                        holding_days=holding_days,
                    )

            # ── 步驟 2: 若有持股名額，執行當日 Point-in-Time 選股 ──
            if self.portfolio.available_slots > 0 and (day_idx % self.scan_interval == 0):
                candidates = []

                for s_id, df in self.stock_data_cache.items():
                    if s_id in self.portfolio.positions:
                        continue

                    # 嚴格僅擷取 curr_date (含) 之前的歷史日 K，杜絕任何未來資訊
                    hist_slice = df[df["date"] <= curr_date]
                    if len(hist_slice) < 252:
                        continue

                    # 前置過濾：近 20 日均量、成交金額與最低股價
                    recent_20 = hist_slice.tail(20)
                    avg_vol = float(recent_20["Volume"].mean())
                    last_close = float(hist_slice["Close"].iloc[-1])
                    turnover_twd = avg_vol * 1000.0 * last_close

                    stock_market = self.stock_meta_cache[s_id].get("market", "listed")
                    criteria = self.settings.get_market_criteria(stock_market)

                    if avg_vol < criteria["min_volume"] or last_close < criteria["min_price"]:
                        continue
                    if self.settings.ENABLE_TURNOVER_FILTER and turnover_twd < criteria["min_turnover"]:
                        continue

                    # 總市值過濾
                    mcap = float(self.stock_meta_cache[s_id].get("market_cap", 0.0))
                    if self.settings.ENABLE_MARKET_CAP_FILTER and mcap > 0 and mcap < criteria["min_market_cap"]:
                        continue

                    # 1 年期 Beta 過濾 (上市比對 TAIEX, 上櫃比對 TPEx)
                    bench_for_stock = self.benchmark_otc_df if stock_market == "otc" else self.benchmark_df
                    if self.settings.ENABLE_BETA_FILTER and not bench_for_stock.empty:
                        bench_slice = bench_for_stock[bench_for_stock["date"] <= curr_date]
                        beta_1y = calculate_beta(hist_slice, bench_slice, lookback_days=252)
                        if beta_1y is not None and beta_1y < criteria["min_beta"]:
                            continue

                    # 1. 檢測 Stage 2 Trend Template
                    tt_result = check_trend_template(hist_slice)
                    if not tt_result.get("is_stage_2_core", False):
                        continue
                    if tt_result["score"] < self.settings.TREND_TEMPLATE_MIN_PASS:
                        continue

                    # 2. 檢測 VCP 波動收斂型態
                    vcp_result = detect_vcp(
                        hist_slice,
                        strict_mode=self.settings.VCP_STRICT_MODE,
                        strict_convergence=self.settings.VCP_STRICT_CONVERGENCE,
                        max_tightness=self.settings.VCP_MAX_TIGHTNESS,
                        max_pivot_distance=self.settings.VCP_MAX_PIVOT_DISTANCE,
                        max_base_depth=self.settings.VCP_MAX_BASE_DEPTH,
                    )
                    if not vcp_result["is_vcp"]:
                        continue

                    # 3. 計算綜合評分
                    score = calculate_score(
                        trend_result=tt_result,
                        vcp_result=vcp_result,
                        df=hist_slice,
                    )

                    candidates.append({
                        "stock_id": s_id,
                        "name": self.stock_meta_cache[s_id]["name"],
                        "score": score,
                        "close": last_close,
                        "pivot_price": vcp_result["pivot_price"],
                        "tightness": vcp_result["tightness"],
                    })

                # 依評分排序，買進 Top N
                if candidates:
                    candidates = rank_results(candidates)
                    for cand in candidates:
                        if self.portfolio.available_slots <= 0:
                            break

                        s_id = cand["stock_id"]
                        s_name = cand["name"]
                        buy_price = cand["close"]
                        pivot_price = cand["pivot_price"]

                        can_buy, shares = self.portfolio.can_buy(s_id, buy_price)
                        if can_buy and shares > 0:
                            initial_stop = self.risk_manager.calculate_initial_stop(buy_price)
                            self.portfolio.execute_buy(
                                stock_id=s_id,
                                name=s_name,
                                date=curr_date,
                                price=buy_price,
                                shares=shares,
                                stop_loss_price=initial_stop,
                                pivot_price=pivot_price,
                            )
                            current_day_prices[s_id] = buy_price

            # ── 步驟 3: 結算當日資產淨值 ──
            bench_price = bench_price_map.get(curr_date)
            self.portfolio.record_daily_equity(
                date=curr_date,
                price_map=current_day_prices,
                benchmark_price=bench_price,
            )

        # ── 步驟 4: 回測結束，強制平倉剩餘持股並結算 ──
        if self.all_trading_dates:
            final_date = self.all_trading_dates[-1]
            remaining_ids = list(self.portfolio.positions.keys())
            for s_id in remaining_ids:
                pos = self.portfolio.positions[s_id]
                df = self.stock_data_cache.get(s_id)
                final_price = float(df["Close"].iloc[-1]) if df is not None and not df.empty else pos.current_price
                self.portfolio.execute_sell(
                    stock_id=s_id,
                    date=final_date,
                    price=final_price,
                    reason="BACKTEST_END_SETTLE",
                    holding_days=len(self.all_trading_dates) - 1,
                )

        equity_df = self.portfolio.get_equity_dataframe()
        metrics = calculate_performance_metrics(
            equity_df=equity_df,
            trades=self.portfolio.trades,
            initial_capital=self.portfolio.initial_capital,
        )

        logger.info("回測執行完成！總報酬率: %+.2f%% | CAGR: %+.2f%% | 勝率: %.1f%% | MDD: %.2f%%",
                    metrics.total_return_pct, metrics.cagr_pct, metrics.win_rate_pct, metrics.max_drawdown_pct)
        return metrics
