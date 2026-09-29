"""VCP 波動收斂型態偵測模組 (VCP Detector Module).

依據 Mark Minervini 的《超級績效》VCP (Volatility Contraction Pattern) 核心理論實作：
1. 逐波振幅收窄 (Price Contraction): T1 > T2 > T3，每波回檔深度逐步遞減
2. 底部抬高或平底支撐 (Higher Lows / Support Floor): 後續波低點不得破底
3. 價量同步萎縮 (Volume Dry-Up): 逐波收縮均量遞減，樞紐突破點前成交量極度萎縮
4. 活躍基底定位 (Active Base Window): 鎖定近期 15~80 交易日 (3~16 週) 的整理基底
5. 型態階段識別 (Action Stage): 區分買點警戒、剛突破初期 (1~3日)、回踩樞紐與過度延伸
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

logger = logging.getLogger(__name__)


def _extract_zigzag_extrema(
    df_base: pd.DataFrame, order: int = 4
) -> List[Tuple[str, int, float]]:
    """提取基底區間內交替出現的波段局部高低點 (Extract alternating peaks and troughs).

    Args:
        df_base: 基底區間價格 DataFrame (需包含 High, Low)
        order: 局部極值檢測窗口階數 (預設 4，約 4~8 天一個轉折點)

    Returns:
        List[Tuple[str, int, float]]: 依時間排序的 (類型 'high'/'low', 索引位置, 價格)
    """
    high_vals = df_base["High"].values if "High" in df_base.columns else df_base["high"].values
    low_vals = df_base["Low"].values if "Low" in df_base.columns else df_base["low"].values

    highs_idx = argrelextrema(high_vals, np.greater_equal, order=order)[0]
    lows_idx = argrelextrema(low_vals, np.less_equal, order=order)[0]

    raw_extrema: List[Tuple[str, int, float]] = []
    for idx in highs_idx:
        raw_extrema.append(("high", int(idx), float(high_vals[idx])))
    for idx in lows_idx:
        raw_extrema.append(("low", int(idx), float(low_vals[idx])))

    raw_extrema.sort(key=lambda x: x[1])

    if not raw_extrema:
        return []

    # 確保高低點嚴格交替 (連續出現同類極值時，高點取最高，低點取最低)
    cleaned_extrema: List[Tuple[str, int, float]] = []
    for kind, idx, price in raw_extrema:
        if not cleaned_extrema:
            cleaned_extrema.append((kind, idx, price))
            continue

        prev_kind, prev_idx, prev_price = cleaned_extrema[-1]
        if kind == prev_kind:
            if (kind == "high" and price > prev_price) or (kind == "low" and price < prev_price):
                cleaned_extrema[-1] = (kind, idx, price)
        else:
            cleaned_extrema.append((kind, idx, price))

    return cleaned_extrema


def _build_contractions_from_extrema(
    df_base: pd.DataFrame,
    extrema: List[Tuple[str, int, float]],
    vol_col: str,
) -> Tuple[List[float], List[Dict[str, Any]], List[float], List[float], List[float]]:
    """從局部極值清單配對 High -> Low 形成收縮波段 (Contractions)."""
    contractions: List[float] = []
    contraction_details: List[Dict[str, Any]] = []
    low_prices: List[float] = []
    high_prices: List[float] = []
    avg_volumes: List[float] = []

    for i in range(len(extrema) - 1):
        if extrema[i][0] == "high" and extrema[i + 1][0] == "low":
            h_idx, h_price = extrema[i][1], extrema[i][2]
            l_idx, l_price = extrema[i + 1][1], extrema[i + 1][2]

            if h_price > 0 and h_price > l_price:
                depth_pct = (h_price - l_price) / h_price * 100.0
                bars = max(1, l_idx - h_idx)
                wave_vol = float(df_base[vol_col].iloc[h_idx : l_idx + 1].mean())

                contractions.append(round(depth_pct, 2))
                low_prices.append(round(l_price, 2))
                high_prices.append(round(h_price, 2))
                avg_volumes.append(wave_vol)

                contraction_details.append({
                    "seq": len(contractions),
                    "high_price": round(h_price, 2),
                    "low_price": round(l_price, 2),
                    "depth_pct": round(depth_pct, 2),
                    "bars": bars,
                    "avg_volume": wave_vol,
                })

    return contractions, contraction_details, low_prices, high_prices, avg_volumes


def _locate_active_base(
    df: pd.DataFrame, max_lookback: int = 90, min_base_days: int = 15
) -> pd.DataFrame:
    """定位當前正在構築的活躍整理基底 (Locate active base window).

    以近期最高點 (Base High / Pivot Origin) 或近期整理區間為起點，
    排除 90 天前無關的舊歷史波段。

    Args:
        df: 歷史價格 DataFrame
        max_lookback: 最大回看天數 (預設 90 天，約 4 個月)
        min_base_days: 基底最少交易日數 (預設 15 天，約 3 週)

    Returns:
        pd.DataFrame: 截取出的活躍基底子 DataFrame
    """
    if len(df) <= min_base_days:
        return df.copy().reset_index(drop=True)

    df_slice = df.iloc[-max_lookback:].copy().reset_index(drop=True)
    high_col = "High" if "High" in df_slice.columns else "high"

    # 尋找近 90 天內的最高點位置
    highest_idx = int(df_slice[high_col].idxmax())

    # 若最高點距離現在太近 (少於 15 天)，代表股價剛創高正在收縮或已突破，往前抓取至少 35 天以包含完整收縮波次
    if len(df_slice) - highest_idx < min_base_days:
        start_idx = max(0, len(df_slice) - max(35, min_base_days))
    else:
        start_idx = highest_idx

    active_base = df_slice.iloc[start_idx:].copy().reset_index(drop=True)
    if len(active_base) < min_base_days:
        active_base = df_slice.copy().reset_index(drop=True)

    return active_base


def detect_vcp(
    df: pd.DataFrame,
    lookback: int = 90,
    strict_mode: bool = False,
    strict_convergence: bool = True,
    max_tightness: float = 12.0,
    max_pivot_distance: float = 8.0,
    max_base_depth: float = 45.0,
    scan_mode: Optional[str] = None,
    include_breakout: bool = True,
    include_retest: bool = True,
    disposition_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """檢測股票價格與成交量是否符合 VCP 波動收斂型態 (Detect VCP pattern).

    核心要素：
    1. 逐波振幅收窄 (Price Contraction Depth): T1 > T2 > T3 (每波跌幅逐步遞減)
    2. 支撐底墊高 (Higher Lows / Floor): 後續低點不得一路破底
    3. 價量同步萎縮 (Volume Dry-Up): 逐波收縮均量遞減，突破或回踩頸線具價量對應性
    4. 緊密度與突破點 (Tightness & Pivot): 最後一波振幅符合門檻，距 Pivot 在發動或合理延伸區
    5. 型態階段判定 (Action Stage): 區分買點警戒 (BUY_READY)、剛突破初期 (RECENT_BREAKOUT)、回踩樞紐 (PIVOT_RETEST) 與過度延伸 (EXTENDED)

    Args:
        df: 歷史價格 DataFrame (需包含 Open, High, Low, Close, Volume)
        lookback: 最大基底回看天數 (預設 90)
        strict_mode: 是否相容舊版嚴格模式開關
        strict_convergence: 是否嚴格要求逐波振幅收縮
        max_tightness: 最後一段收縮最大容許振幅百分比
        max_pivot_distance: 距離 Pivot 突破點最大距離百分比
        max_base_depth: 基底最大容許回檔深度百分比
        scan_mode: 篩選模式 ('strict', 'standard', 'loose')，優先於 strict_mode
        include_breakout: 是否將 1~3 日內剛突破 (+0~8%) 之個股納入 VCP 通過名單
        include_retest: 是否將回踩樞紐有守之個股納入 VCP 通過名單

    Returns:
        Dict[str, Any]: 包含 is_vcp, action_stage, contractions, tightness, pivot_price 等指標
    """
    result: Dict[str, Any] = {
        "is_vcp": False,
        "action_stage": "UNCONFIRMED",
        "action_stage_desc": "未確認形態",
        "contractions": [],
        "num_contractions": 0,
        "contraction_details": [],
        "volume_declining": False,
        "higher_lows": False,
        "is_converging": False,
        "tightness": 0.0,
        "pivot_price": 0.0,
        "distance_to_pivot": 0.0,
        "base_depth_pct": 0.0,
        "scan_mode": "standard",
    }

    if df.empty or len(df) < 30:
        return result

    # 1. 解析掃描模式 (Mode Resolution) 與門檻設定
    resolved_mode = (scan_mode or ("strict" if strict_mode else "standard")).lower()
    if resolved_mode == "strict":
        eff_max_tightness = 8.0 if max_tightness == 12.0 else max_tightness
        eff_max_pivot_dist = 5.0 if max_pivot_distance == 8.0 else max_pivot_distance
        eff_max_base_depth = 35.0 if max_base_depth == 45.0 else max_base_depth
        eff_strict_convergence = True
        min_pivot_dist = -2.0
    elif resolved_mode == "loose":
        eff_max_tightness = 15.0 if max_tightness == 12.0 else max_tightness
        eff_max_pivot_dist = 10.0 if max_pivot_distance == 8.0 else max_pivot_distance
        eff_max_base_depth = 50.0 if max_base_depth == 45.0 else max_base_depth
        eff_strict_convergence = False
        min_pivot_dist = -5.0
    else:  # standard
        eff_max_tightness = max_tightness
        eff_max_pivot_dist = max_pivot_distance
        eff_max_base_depth = max_base_depth
        eff_strict_convergence = strict_convergence
        min_pivot_dist = -3.0

    result["scan_mode"] = resolved_mode

    # 2. 取得活躍基底
    df_base = _locate_active_base(df, max_lookback=lookback, min_base_days=18)
    close_col = "Close" if "Close" in df_base.columns else "close"
    high_col = "High" if "High" in df_base.columns else "high"
    low_col = "Low" if "Low" in df_base.columns else "low"
    vol_col = "Volume" if "Volume" in df_base.columns else "volume"

    current_close = float(df_base[close_col].iloc[-1])
    base_high = float(df_base[high_col].max())
    base_low = float(df_base[low_col].min())
    base_depth_all = ((base_high - base_low) / base_high * 100.0) if base_high > 0 else 0.0
    base_days = len(df_base)

    # 3. 多週期動態波次提取 (Multi-Order Adaptive Zigzag)
    # 以「收縮波次數量 (len(contractions) >= 2)」作為降階標準，依序嘗試 order=4, 3, 2
    best_extrema: List[Tuple[str, int, float]] = []
    best_contractions: List[float] = []
    best_details: List[Dict[str, Any]] = []
    best_lows: List[float] = []
    best_highs: List[float] = []
    best_vols: List[float] = []

    for ord_val in [4, 3, 2]:
        ext = _extract_zigzag_extrema(df_base, order=ord_val)
        c, cd, lp, hp, av = _build_contractions_from_extrema(df_base, ext, vol_col)
        if len(c) >= 2:
            best_extrema, best_contractions, best_details, best_lows, best_highs, best_vols = (
                ext, c, cd, lp, hp, av
            )
            # 若已有 2 波以上且最後波收縮度小於等於容許上限，即已達到良好精度
            if c[-1] <= eff_max_tightness:
                break
        elif len(c) > len(best_contractions) or not best_extrema:
            best_extrema, best_contractions, best_details, best_lows, best_highs, best_vols = (
                ext, c, cd, lp, hp, av
            )

    contractions = best_contractions
    contraction_details = best_details
    low_prices = best_lows
    high_prices = best_highs
    avg_volumes = best_vols

    # 只保留近 2~4 次收縮波 (去除過於久遠的早期波段)
    if len(contractions) > 4:
        contractions = contractions[-4:]
        low_prices = low_prices[-4:]
        high_prices = high_prices[-4:]
        avg_volumes = avg_volumes[-4:]
        contraction_details = contraction_details[-4:]
        for idx, item in enumerate(contraction_details):
            item["seq"] = idx + 1

    num_contractions = len(contractions)

    # 4. 根據收縮波次計算 Pivot 與關鍵指標 (支援 0T / 1T / 2T~4T)
    if num_contractions >= 1:
        pivot_price = float(max(high_prices))
        tightness = float(contractions[-1])
        base_depth = float(max(contractions))
    else:
        pivot_price = base_high
        tightness = round(((base_high - current_close) / base_high * 100.0), 2) if base_high > 0 else 0.0
        base_depth = base_depth_all

    distance_to_pivot = ((pivot_price - current_close) / current_close * 100.0) if current_close > 0 else 0.0

    # 5. 【檢驗 1：逐波振幅收斂性 (Contraction Convergence)】
    is_converging = False
    if num_contractions >= 2:
        is_converging = True
        for i in range(1, num_contractions):
            if contractions[i] > contractions[i - 1] * 1.10:
                is_converging = False
                break
        if contractions[-1] > contractions[0] * 0.85 and contractions[-1] > 6.0:
            is_converging = False

    # 6. 【檢驗 2：底底高或平底支撐 (Higher Lows / Support Floor)】
    higher_lows = False
    if num_contractions >= 2:
        higher_lows = True
        for i in range(1, len(low_prices)):
            if low_prices[i] < low_prices[i - 1] * 0.96:
                higher_lows = False
                break
    elif num_contractions == 1:
        higher_lows = bool(current_close >= low_prices[0])

    # 7. 【檢驗 3：價量同步萎縮 (Volume Dry-Up)】
    base_avg_vol = float(df_base[vol_col].mean()) if not df_base.empty else 1.0
    last_wave_vol = avg_volumes[-1] if avg_volumes else base_avg_vol
    vol_50d = float(df[vol_col].tail(50).mean()) if len(df) >= 50 else base_avg_vol
    recent_5d_vol = float(df[vol_col].tail(5).mean()) if len(df) >= 5 else last_wave_vol

    vol_shrinking_across_waves = bool(avg_volumes[-1] <= avg_volumes[0] * 1.05) if len(avg_volumes) >= 2 else True
    vol_dry_up_at_pivot = bool(recent_5d_vol <= vol_50d * 0.95) or bool(last_wave_vol <= base_avg_vol * 0.95)
    volume_declining = vol_shrinking_across_waves and vol_dry_up_at_pivot

    # 8. 【突破 (Breakout) 與 回踩 (Retest) 動態追蹤】
    recent_bars = min(5, len(df))
    recent_slice = df.tail(recent_bars)
    high_all_col = "High" if "High" in recent_slice.columns else "high"
    vol_all_col = "Volume" if "Volume" in recent_slice.columns else "volume"

    recent_broke_pivot = bool(any(recent_slice[high_all_col] >= pivot_price * 0.995))
    recent_vol_max = float(recent_slice[vol_all_col].max()) if not recent_slice.empty else 0.0
    vol_expansion = bool(recent_vol_max >= vol_50d * 1.15)

    # 判定型態進程階段 (vcp_action_stage)
    action_stage = "FORMING"
    action_stage_desc = "基底整理收縮中"

    if distance_to_pivot < -8.0:
        action_stage = "EXTENDED"
        action_stage_desc = "⚠️ 漲幅過度延伸 (已脫離買點超過 +8%，嚴禁追高)"
    elif recent_broke_pivot and abs(distance_to_pivot) <= 3.5 and num_contractions >= 2:
        action_stage = "PIVOT_RETEST"
        action_stage_desc = "🔄 回踩樞紐有守 (突破後回測頸線支撐，二次買點機會)"
    elif recent_broke_pivot and (-8.0 <= distance_to_pivot <= 0.0) and num_contractions >= 2:
        action_stage = "RECENT_BREAKOUT"
        action_stage_desc = "🚀 突破發動初期 (1~3日內帶量突破樞紐，短線主升段)"
    elif (min_pivot_dist <= distance_to_pivot <= eff_max_pivot_dist) and num_contractions >= 2 and tightness <= eff_max_tightness:
        action_stage = "BUY_READY"
        action_stage_desc = "🎯 買點警戒區 (波動收斂緊密，蓄勢帶量突破頸線)"
    elif distance_to_pivot > eff_max_pivot_dist and num_contractions >= 2:
        action_stage = "FORMING"
        action_stage_desc = f"⏳ 基底收縮中 (距樞紐 {distance_to_pivot:+.1f}%，等待靠近發動點)"
    else:
        action_stage = "UNCONFIRMED"
        action_stage_desc = "❌ 形態未成型或波動擴大"

    # 9. 【綜合判定 VCP 是否成立與失敗原因】
    failure_reasons: List[str] = []

    if num_contractions < 2:
        if num_contractions == 1:
            failure_reasons.append(f"收縮波次不足: 目前僅完成第 1 波回檔 (1T: -{contractions[0]:.1f}%)，尚需後續收斂波")
        else:
            failure_reasons.append("收縮波次不足: 近期未見交替波段收縮 (0T)，處於單邊或無序整理")

    if num_contractions >= 2 and not is_converging:
        failure_reasons.append("振幅未逐波縮窄: 後續波段跌幅大於前波 (波動未收斂)")
    if num_contractions >= 2 and not higher_lows:
        failure_reasons.append("支撐破底: 回檔波段低點一路破底，未見底底高支撐")
    if not volume_declining and action_stage != "RECENT_BREAKOUT":
        failure_reasons.append("量能未沉澱: 收縮期間或突破前夕未見明顯量縮")
    if tightness > eff_max_tightness:
        failure_reasons.append(f"終波波動仍大: 最後波收縮度 ({tightness:.1f}%) 超過容許上限 ({eff_max_tightness:.1f}%)")
    if action_stage == "EXTENDED":
        failure_reasons.append(f"已大幅突破樞紐點: 當前價位距 Pivot 已延伸 {abs(distance_to_pivot):.1f}% (超過 +8% 買點上限，嚴禁追高)")
    elif distance_to_pivot > eff_max_pivot_dist:
        failure_reasons.append(f"距樞紐點過遠: 當前距 Pivot 突破價達 {distance_to_pivot:+.1f}% (尚未進入發動買點區)")
    if base_depth > eff_max_base_depth:
        failure_reasons.append(f"形態回檔過深: 基底最大跌幅 ({base_depth:.1f}%) 超過容許上限 ({eff_max_base_depth:.1f}%)")

    # 基礎形態要素檢核
    convergence_passed = is_converging if eff_strict_convergence else (tightness <= eff_max_tightness)
    pattern_valid = bool(
        num_contractions >= 2
        and convergence_passed
        and higher_lows
        and base_depth <= eff_max_base_depth
        and tightness <= eff_max_tightness
    )

    # 量能檢核：突破初期允許爆量，回踩與買點區要求量縮
    if action_stage == "RECENT_BREAKOUT":
        vol_passed = vol_expansion or volume_declining
    else:
        vol_passed = volume_declining

    # 是否符合可操作 VCP 標的
    is_actionable = False
    if pattern_valid and vol_passed:
        if action_stage == "BUY_READY":
            is_actionable = True
        elif action_stage == "RECENT_BREAKOUT" and include_breakout:
            is_actionable = True
        elif action_stage == "PIVOT_RETEST" and include_retest:
            is_actionable = True

    is_vcp = is_actionable

    # 10. 產出 VCP 關鍵要素檢核清單 (供 Console 與 Telegram 報表使用)
    checklist = [
        {
            "name": "收縮波次充足 (2~4T)",
            "passed": num_contractions >= 2,
            "detail": f"{num_contractions}T ({'充足' if num_contractions >= 2 else '波次不足'})",
        },
        {
            "name": "逐波振幅收縮",
            "passed": is_converging,
            "detail": "逐波收窄成立" if is_converging else "振幅未收窄或中途擴大",
        },
        {
            "name": "底部支撐底底高",
            "passed": higher_lows,
            "detail": "低點墊高未破底" if higher_lows else "波段低點破底",
        },
        {
            "name": "價量關係良好",
            "passed": vol_passed,
            "detail": "量能符合型態進程" if vol_passed else "整理期間量能未縮",
        },
        {
            "name": f"終波極致緊縮 (<= {eff_max_tightness:.0f}%)",
            "passed": (tightness <= eff_max_tightness and num_contractions >= 2),
            "detail": f"{tightness:.1f}% ({'緊密達標' if tightness <= eff_max_tightness else '波動仍大'})",
        },
        {
            "name": f"進場發動區間 ({min_pivot_dist:.0f}% ~ +{eff_max_pivot_dist:.0f}%)",
            "passed": (min_pivot_dist <= distance_to_pivot <= eff_max_pivot_dist) or (action_stage in ("RECENT_BREAKOUT", "PIVOT_RETEST")),
            "detail": f"{distance_to_pivot:+.1f}% ({action_stage})",
        },
        {
            "name": f"基底深度適中 (<= {eff_max_base_depth:.0f}%)",
            "passed": base_depth <= eff_max_base_depth,
            "detail": f"{base_depth:.1f}% ({'深度適中' if base_depth <= eff_max_base_depth else '回檔過深'})",
        },
    ]

    is_disposed = bool(disposition_info and disposition_info.get("stock_id"))
    if is_disposed:
        interval = disposition_info.get("matching_interval", "分盤撮合")
        rem_days = disposition_info.get("remaining_trading_days", 0)
        end_d = disposition_info.get("end_date", "")
        checklist.append({
            "name": "處置狀態與交易限制",
            "passed": True,
            "detail": f"🚨 {interval} / 剩餘 {rem_days} 日 (至 {end_d}，量縮受管制影響)",
        })

    if is_vcp:
        vcp_status_desc = f"{action_stage_desc} ({num_contractions}T: {'→'.join(f'{c}%' for c in contractions)})"
    elif action_stage == "EXTENDED":
        vcp_status_desc = f"{action_stage_desc} ({num_contractions}T, 距Pivot {distance_to_pivot:+.1f}%)"
    elif num_contractions == 1:
        vcp_status_desc = f"⏳ 完成初次回檔 (1T: -{contractions[0]:.1f}%)，待第 2 波收縮形成基底"
    elif num_contractions >= 2:
        vcp_status_desc = f"❌ 未達標準 VCP 型態 ({num_contractions}T: {', '.join(failure_reasons[:2])})"
    else:
        vcp_status_desc = "❌ 無明顯波段收縮結構 (0T)"

    result["is_vcp"] = is_vcp
    result["action_stage"] = action_stage
    result["action_stage_desc"] = action_stage_desc
    result["contractions"] = [round(c, 1) for c in contractions]
    result["num_contractions"] = num_contractions
    result["contraction_details"] = contraction_details
    result["volume_declining"] = volume_declining
    result["higher_lows"] = higher_lows
    result["is_converging"] = is_converging
    result["tightness"] = round(tightness, 2)
    result["pivot_price"] = round(pivot_price, 2)
    result["distance_to_pivot"] = round(distance_to_pivot, 2)
    result["base_depth_pct"] = round(base_depth, 2)
    result["base_high"] = round(base_high, 2)
    result["base_low"] = round(base_low, 2)
    result["base_days"] = base_days
    result["vcp_status_desc"] = vcp_status_desc
    result["checklist"] = checklist
    result["failure_reasons"] = failure_reasons
    result["is_disposed"] = is_disposed
    result["disposition_info"] = disposition_info

    return result
