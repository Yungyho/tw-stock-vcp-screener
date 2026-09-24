"""趨勢模板檢測模組 (Trend Template Module).

實作 Mark Minervini 的 9 項 Stage 2 (第二階段上升趨勢) 趨勢模板檢測。
區分「核心不可妥協條件 (Core Stage 2 Criteria)」與「彈性輔助條件 (Flexible Criteria)」，
確保絕不選入長期均線向下或處於空頭階段 (Stage 4 / Stage 1) 的股票。
"""

import numpy as np
import pandas as pd


def check_trend_template(df: pd.DataFrame) -> dict:
    """檢測個股是否符合 Mark Minervini 的 Stage 2 趨勢模板.

    9 項條件說明：
    【核心不可妥協條件 (Core Stage 2)】：
    1. 股價 > 200 日均線 (Close > SMA200)
    2. 股價 > 150 日均線 (Close > SMA150)
    3. 150 日均線 > 200 日均線 (SMA150 > SMA200)
    4. 200 日均線長期趨勢向上 (SMA200 至少較 22 日前持平或微幅向上)
    5. 股價距 52 週最低點至少 +25% (Close >= 52W_Low * 1.25)

    【彈性輔助條件 (Flexible)】：
    6. 50 日均線 > 150 日均線 (SMA50 > SMA150)
    7. 50 日均線 > 200 日均線 (SMA50 > SMA200)
    8. 股價 > 50 日均線 (Close > SMA50)
    9. 股價距 52 週最高點在 25% 內 (Close >= 52W_High * 0.75)

    Args:
        df: 包含 OHLCV 價格資料的 DataFrame (需至少 252 個交易日)

    Returns:
        dict: 包含 passed, is_stage_2_core, score, conditions, details
    """
    result = {
        "passed": False,
        "is_stage_2_core": False,
        "conditions": {},
        "score": 0,
        "details": "",
    }

    if df.empty or len(df) < 252:
        result["details"] = "歷史資料不足 252 交易日，無法計算年線與 52 週指標。"
        return result

    price_col = "Adj Close" if "Adj Close" in df.columns else "Close"

    # 計算各週期移動平均線與 52 週高低點
    sma50 = df[price_col].rolling(window=50).mean()
    sma150 = df[price_col].rolling(window=150).mean()
    sma200 = df[price_col].rolling(window=200).mean()
    high_52w = df["High"].rolling(window=252).max()
    low_52w = df["Low"].rolling(window=252).min()

    # 取得最新一筆數值
    current_close = float(df[price_col].iloc[-1])
    current_sma50 = float(sma50.iloc[-1])
    current_sma150 = float(sma150.iloc[-1])
    current_sma200 = float(sma200.iloc[-1])
    current_high_52w = float(high_52w.iloc[-1])
    current_low_52w = float(low_52w.iloc[-1])

    # 取得 22 交易日前 (約 1 個月) 的 200MA 數值以判斷長期均線斜率
    past_sma200 = float(sma200.iloc[-23]) if len(sma200) >= 23 else np.nan

    # ── 條件判定 ──
    # 1. 股價高於 150MA
    c1 = current_close > current_sma150
    # 2. 股價高於 200MA (長期趨勢向上基石)
    c2 = current_close > current_sma200
    # 3. 150MA 高於 200MA (均線中長期多頭排列)
    c3 = current_sma150 > current_sma200
    # 4. 200MA 斜率向上 (至少持平或上升，絕不能向下翻空)
    c4 = pd.notna(past_sma200) and (current_sma200 >= past_sma200 * 0.998)
    # 5. 50MA 高於 150MA
    c5 = current_sma50 > current_sma150
    # 6. 50MA 高於 200MA
    c6 = current_sma50 > current_sma200
    # 7. 股價高於 50MA
    c7 = current_close > current_sma50
    # 8. 股價高於 52 週最低點至少 25% (脫離破底低迷期)
    c8 = current_close >= (current_low_52w * 1.25)
    # 9. 股價距離 52 週最高點在 25% 之內 (維持強者恆強)
    c9 = current_close >= (current_high_52w * 0.75)

    conditions = {
        "close > SMA150": {"passed": bool(c1), "value": current_close, "threshold": current_sma150, "core": True},
        "close > SMA200": {"passed": bool(c2), "value": current_close, "threshold": current_sma200, "core": True},
        "SMA150 > SMA200": {"passed": bool(c3), "value": current_sma150, "threshold": current_sma200, "core": True},
        "SMA200_rising": {"passed": bool(c4), "value": current_sma200, "threshold": past_sma200, "core": True},
        "SMA50 > SMA150": {"passed": bool(c5), "value": current_sma50, "threshold": current_sma150, "core": False},
        "SMA50 > SMA200": {"passed": bool(c6), "value": current_sma50, "threshold": current_sma200, "core": False},
        "close > SMA50": {"passed": bool(c7), "value": current_close, "threshold": current_sma50, "core": False},
        "above_52w_low_by_25%": {"passed": bool(c8), "value": current_close, "threshold": current_low_52w * 1.25, "core": True},
        "within_25%_of_52w_high": {"passed": bool(c9), "value": current_close, "threshold": current_high_52w * 0.75, "core": False},
    }

    # 核心 5 大條件全部通過才視為確認處於 Stage 2 上升趨勢
    is_stage_2_core = bool(c1 and c2 and c3 and c4 and c8)

    passed_count = sum(1 for c in conditions.values() if c["passed"])

    result["conditions"] = conditions
    result["score"] = passed_count
    result["is_stage_2_core"] = is_stage_2_core
    result["passed"] = passed_count == 9
    result["details"] = f"通過 {passed_count}/9 項條件 (Stage 2 核心多頭: {'是' if is_stage_2_core else '否'})"

    return result
