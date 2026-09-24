"""市場階段分析模組單元測試 (Unit Tests for Market Stage Analyzer)."""

import numpy as np
import pandas as pd
import pytest

from src.stage_analyzer import analyze_market_stage


def test_stage_2_uptrend_detection():
    """測試 Stage 2 強勢多頭上升階段判定."""
    dates = pd.date_range("2023-01-01", periods=300, freq="B").strftime("%Y-%m-%d")

    # 股價從 100 漲到 250 (強勢多頭)
    closes = [100.0 * (1.0 + 0.003 * i) for i in range(300)]
    highs = [c * 1.02 for c in closes]
    lows = [c * 0.98 for c in closes]
    opens = [c * 0.99 for c in closes]
    volumes = [1000] * 300

    df = pd.DataFrame({
        "date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })

    res = analyze_market_stage(df)
    assert res.stage == 2
    assert "STAGE 2" in res.stage_name
    assert res.is_ma_bullish is True
    assert res.sma200_slope_pct > 0
    assert len(res.stage_reasons) > 0
    assert res.stage_sub_status != ""


def test_stage_4_downtrend_detection():
    """測試 Stage 4 空頭下跌破底階段判定."""
    dates = pd.date_range("2023-01-01", periods=300, freq="B").strftime("%Y-%m-%d")

    # 股價從 200 跌到 80 (持續破底)
    closes = [200.0 * (1.0 - 0.003 * i) for i in range(300)]
    highs = [c * 1.02 for c in closes]
    lows = [c * 0.98 for c in closes]
    opens = [c * 1.01 for c in closes]
    volumes = [1000] * 300

    df = pd.DataFrame({
        "date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })

    res = analyze_market_stage(df)
    assert res.stage == 4
    assert "STAGE 4" in res.stage_name
    assert res.sma200_slope_pct < 0
    assert "空頭" in res.stage_name
    assert len(res.stage_reasons) > 0


def test_stage_1_accumulation_detection():
    """測試 Stage 1 底部打底盤整階段判定."""
    dates = pd.date_range("2023-01-01", periods=300, freq="B").strftime("%Y-%m-%d")

    # 股價在 50 元附近狹幅橫盤震盪 (50 +- 1 元)
    np.random.seed(42)
    noise = np.random.normal(0, 0.5, 300)
    closes = [50.0 + noise[i] for i in range(300)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    opens = closes.copy()
    volumes = [500] * 300

    df = pd.DataFrame({
        "date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })

    res = analyze_market_stage(df)
    assert res.stage == 1
    assert "STAGE 1" in res.stage_name
    assert "打底" in res.stage_name


def test_stage_3_distribution_detection():
    """測試 Stage 3 高檔頭部震盪出貨階段判定."""
    dates = pd.date_range("2023-01-01", periods=300, freq="B").strftime("%Y-%m-%d")

    # 前 220 天大漲 (100 -> 250)，隨後在高檔做頭並拉回 20%
    closes = [100.0 * (1.0 + 0.005 * i) for i in range(220)]  # 100 -> 210
    peak = closes[-1]
    # 後 80 天震盪回檔跌破 50MA 至 165
    decline = [peak - (peak - 165.0) * (j / 80.0) for j in range(80)]
    all_closes = closes + decline

    highs = [c * 1.02 for c in all_closes]
    lows = [c * 0.98 for c in all_closes]
    opens = all_closes.copy()
    volumes = [2000] * 300

    df = pd.DataFrame({
        "date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": all_closes,
        "Volume": volumes,
    })

    res = analyze_market_stage(df)
    assert res.stage == 3
    assert "STAGE 3" in res.stage_name
    assert "頭部" in res.stage_name


def test_stage_insufficient_data():
    """測試歷史數據不足 252 交易日時回傳 Stage 0."""
    df = pd.DataFrame({
        "Close": [100.0] * 100,
        "High": [102.0] * 100,
        "Low": [98.0] * 100,
    })
    res = analyze_market_stage(df)
    assert res.stage == 0
    assert "STAGE 0" in res.stage_name
