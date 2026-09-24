"""VCP 波動收斂與價量檢測演算法單元測試 (Unit Tests for VCP Convergence & Higher Lows)."""

import numpy as np
import pandas as pd
import pytest

from src.vcp_detector import detect_vcp


def test_valid_vcp_convergence():
    """測試標準 3T 價量收斂型態 (T1: 20% -> T2: 10% -> T3: 4%，底底高，量縮)."""
    dates = pd.date_range("2024-01-01", periods=60, freq="B").strftime("%Y-%m-%d")

    closes = np.linspace(100, 80, 11).tolist()[:-1]  # 0..9
    closes += np.linspace(80, 98, 11).tolist()[:-1]   # 10..19
    closes += np.linspace(98, 88, 11).tolist()[:-1]   # 20..29
    closes += np.linspace(88, 99, 11).tolist()[:-1]   # 30..39
    closes += np.linspace(99, 95, 11).tolist()[:-1]   # 40..49
    closes += np.linspace(95, 98.5, 11).tolist()      # 50..60

    closes = closes[:60]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    opens = [c * 0.995 for c in closes]
    volumes = [1000] * 20 + [700] * 20 + [400] * 20

    df = pd.DataFrame({
        "date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })

    res = detect_vcp(df, strict_convergence=True)
    assert res["is_vcp"] is True
    assert res["is_converging"] is True
    assert res["higher_lows"] is True
    assert res["tightness"] <= 8.0
    assert len(res["checklist"]) == 7


def test_expanding_volatility_rejection():
    """測試震盪擴大型態 (如 2486 特徵：20% -> 25% -> 38% 破底)，應被嚴格排除."""
    dates = pd.date_range("2024-01-01", periods=60, freq="B").strftime("%Y-%m-%d")

    closes = np.linspace(100, 82, 11).tolist()[:-1]
    closes += np.linspace(82, 95, 11).tolist()[:-1]
    closes += np.linspace(95, 70, 11).tolist()[:-1]
    closes += np.linspace(70, 90, 11).tolist()[:-1]
    closes += np.linspace(90, 55, 11).tolist()[:-1]
    closes += np.linspace(55, 65, 11).tolist()

    closes = closes[:60]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    opens = [c * 0.995 for c in closes]
    volumes = [1000] * 60

    df = pd.DataFrame({
        "date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })

    res = detect_vcp(df, strict_convergence=True)
    assert res["is_vcp"] is False
    assert (not res["is_converging"]) or (not res["higher_lows"])
    assert len(res["failure_reasons"]) > 0
    assert res["pivot_price"] > 0
    assert res["base_depth_pct"] > 0


def test_single_contraction_1t_handling():
    """測試單波回檔 (1T) 之股票依然提供完整基底數據與診斷，且 is_vcp 為 False."""
    dates = pd.date_range("2024-01-01", periods=45, freq="B").strftime("%Y-%m-%d")

    # 100 -> 85 (-15%) -> 90
    closes = np.linspace(100, 85, 23).tolist()[:-1] + np.linspace(85, 92, 23).tolist()
    closes = closes[:45]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    opens = [c * 0.995 for c in closes]
    volumes = [500] * 45

    df = pd.DataFrame({
        "date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })

    res = detect_vcp(df)
    assert res["is_vcp"] is False
    assert res["pivot_price"] > 0
    assert res["base_depth_pct"] > 0
    assert res["distance_to_pivot"] > 0
    assert len(res["failure_reasons"]) > 0
    assert any("波次不足" in r for r in res["failure_reasons"])


def test_zero_contraction_metrics():
    """測試無交替波段之單邊/橫盤股票依然能產出有效 Pivot 與基底深度."""
    dates = pd.date_range("2024-01-01", periods=40, freq="B").strftime("%Y-%m-%d")

    # 股價緩步單邊下跌 50 -> 42
    closes = np.linspace(50, 42, 40).tolist()
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    opens = closes.copy()
    volumes = [200] * 40

    df = pd.DataFrame({
        "date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })

    res = detect_vcp(df)
    assert res["is_vcp"] is False
    assert res["pivot_price"] > 0
    assert res["base_depth_pct"] > 0
    assert res["distance_to_pivot"] > 0


def test_multi_order_adaptive_contraction_detection():
    """測試多週期動態降階能成功辨識出較細微的收縮波次 (如 3T 收斂)."""
    # 構造 3 次交替收縮:
    # 100 -> 85 (-15%) -> 95 -> 87 (-8.4%) -> 94 -> 90 (-4.2%) -> 93.5
    closes = np.linspace(100, 85, 9).tolist()[:-1]
    closes += np.linspace(85, 95, 9).tolist()[:-1]
    closes += np.linspace(95, 87, 9).tolist()[:-1]
    closes += np.linspace(87, 94, 9).tolist()[:-1]
    closes += np.linspace(94, 90, 8).tolist()[:-1]
    closes += np.linspace(90, 93.5, 8).tolist()

    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B").strftime("%Y-%m-%d")
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    opens = closes.copy()
    volumes = [1000] * 18 + [600] * 18 + [300] * (len(closes) - 36)

    df = pd.DataFrame({
        "date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })

    res = detect_vcp(df, scan_mode="standard")
    assert res["num_contractions"] >= 2
    assert res["tightness"] <= 12.0
    assert res["action_stage"] in ("BUY_READY", "FORMING")


def test_vcp_scan_modes():
    """測試 Standard, Loose, Strict 三種模式的門檻生效情況."""
    # 構造最後波收縮度約 13.5% (在 12%~15% 之間)
    closes = np.linspace(100, 75, 13).tolist()[:-1]   # T1: -25%
    closes += np.linspace(75, 95, 13).tolist()[:-1]
    closes += np.linspace(95, 82.2, 13).tolist()[:-1] # T2: -13.5%
    closes += np.linspace(82.2, 92, 14).tolist()

    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B").strftime("%Y-%m-%d")
    highs = [c * 1.008 for c in closes]
    lows = [c * 0.992 for c in closes]
    opens = closes.copy()
    volumes = [1000] * 25 + [400] * (len(closes) - 25)

    df = pd.DataFrame({
        "date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })

    # Strict: 門檻 8%，13.5% 應失敗
    res_strict = detect_vcp(df, scan_mode="strict")
    assert res_strict["is_vcp"] is False

    # Loose: 門檻 15%，13.5% 應通過
    res_loose = detect_vcp(df, scan_mode="loose")
    assert res_loose["is_vcp"] is True
    assert res_loose["scan_mode"] == "loose"


def test_vcp_action_stages():
    """測試型態階段辨識: BUY_READY 與 EXTENDED (過度延伸)."""
    # 構造收斂後突破，最後收在 120 (Pivot 約 95，延伸超過 20%)
    closes = np.linspace(100, 80, 11).tolist()[:-1]
    closes += np.linspace(80, 95, 11).tolist()[:-1]
    closes += np.linspace(95, 88, 11).tolist()[:-1]
    closes += np.linspace(88, 95, 11).tolist()[:-1]
    closes += np.linspace(95, 120, 11).tolist()

    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B").strftime("%Y-%m-%d")
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    opens = closes.copy()
    volumes = [1000] * 40 + [3000] * (len(closes) - 40)

    df = pd.DataFrame({
        "date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })

    res = detect_vcp(df)
    assert res["action_stage"] == "EXTENDED"
    assert res["is_vcp"] is False
    assert any("已大幅突破" in r for r in res["failure_reasons"])

