"""Beta 值計算模組 (Beta Calculation Module).

計算個股相對於台股加權指數 (^TWII) 之 1 年期 (約 252 個交易日) Beta 波動度敏感係數。
公式：
    Beta = Cov(R_stock, R_market) / Var(R_market)
其中 R 為每日報酬率。
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def calculate_beta(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    lookback_days: int = 252,
) -> Optional[float]:
    """計算個股相對於大盤指數之 1 年期 Beta 值.

    Args:
        stock_df: 個股價格 DataFrame (需包含 date 與 Close 或 Adj Close 欄位)
        benchmark_df: 大盤基準價格 DataFrame (^TWII)
        lookback_days: 回看交易日數 (預設 252 日，約 1 年)

    Returns:
        Optional[float]: 計算出的 Beta 值 (例如 1.25)，若資料不足或無法計算則回傳 None
    """
    if stock_df.empty or benchmark_df.empty:
        return None

    try:
        s_price_col = "Close" if "Close" in stock_df.columns else "close"
        b_price_col = "Close" if "Close" in benchmark_df.columns else "close"
        s_date_col = "date" if "date" in stock_df.columns else "Date"
        b_date_col = "date" if "date" in benchmark_df.columns else "Date"

        # 整理個股報酬率
        s_df = stock_df[[s_date_col, s_price_col]].copy()
        s_df.columns = ["date", "stock_close"]
        s_df["date"] = s_df["date"].astype(str).str[:10]
        s_df = s_df.sort_values("date").drop_duplicates("date")
        s_df["stock_ret"] = s_df["stock_close"].pct_change()

        # 整理大盤報酬率
        b_df = benchmark_df[[b_date_col, b_price_col]].copy()
        b_df.columns = ["date", "bench_close"]
        b_df["date"] = b_df["date"].astype(str).str[:10]
        b_df = b_df.sort_values("date").drop_duplicates("date")
        b_df["bench_ret"] = b_df["bench_close"].pct_change()

        # 依日期合併並取最近 lookback_days
        merged = pd.merge(s_df, b_df, on="date", how="inner").dropna()
        if len(merged) < min(60, lookback_days // 2):
            return None

        recent_merged = merged.tail(lookback_days)
        s_rets = recent_merged["stock_ret"].values
        b_rets = recent_merged["bench_ret"].values

        var_bench = np.var(b_rets, ddof=1)
        if var_bench <= 1e-9:
            return None

        cov_matrix = np.cov(s_rets, b_rets)
        cov_sb = cov_matrix[0, 1]

        beta = float(cov_sb / var_bench)
        return round(beta, 2)
    except Exception as e:
        logger.debug("計算 Beta 值時發生例外: %s", e)
        return None
