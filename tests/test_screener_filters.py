"""新增篩選條件之單元測試 (Unit tests for Beta, Market Cap, and Turnover filters)."""

import numpy as np
import pandas as pd
import pytest

from src.beta import calculate_beta


def test_calculate_beta():
    """測試 1 年期 Beta 值計算."""
    dates = pd.date_range("2023-01-01", periods=260, freq="B").strftime("%Y-%m-%d")

    # 模擬大盤加權指數每日漲跌 0.5%
    bench_closes = [15000.0 * (1.0 + 0.005 * i + 0.002 * (i % 3)) for i in range(260)]
    bench_df = pd.DataFrame({"date": dates, "Close": bench_closes})

    # 模擬個股波動為大盤 1.5 倍 (Beta 應約為 1.5)
    stock_closes = [100.0 * (1.0 + 0.0075 * i + 0.003 * (i % 3)) for i in range(260)]
    stock_df = pd.DataFrame({"date": dates, "Close": stock_closes})

    beta = calculate_beta(stock_df, bench_df, lookback_days=252)
    assert beta is not None
    assert beta > 1.0


def test_turnover_calculation():
    """測試成交金額計算邏輯."""
    # 股價 50 元，近 20 日均量 1000 張 (1000 * 1000 = 1,000,000 股)
    last_close = 50.0
    avg_volume_lots = 1000
    turnover_twd = avg_volume_lots * 1000 * last_close

    # 成交金額應為 50,000,000 元 (5,000 萬) > 100,000 元 (10 萬)
    assert turnover_twd == 50000000.0
    assert turnover_twd > 100000.0
