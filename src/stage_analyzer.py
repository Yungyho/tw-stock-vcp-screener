"""市場趨勢四階段分析模組 (Market Trend 4-Stage Analyzer Module).

依據 Stan Weinstein 與 Mark Minervini 的《超級績效》四階段理論，判定個股當前處於：
- STAGE 1: 打底築底期 (Bottoming / Accumulation)
- STAGE 2: 上升多頭期 (Uptrend / Mark-Up) —— 最佳 VCP 買進階段
- STAGE 3: 高檔頭部期 (Topping / Distribution)
- STAGE 4: 下跌空頭期 (Downtrend / Mark-Down)
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class StageAnalysisResult:
    """階段分析結果資料模型."""

    stage: int  # 1, 2, 3, 4 (0 代表資料不足)
    stage_name: str  # e.g. "STAGE 2 (強勢上升階段 / Mark-Up)"
    stage_sub_status: str  # e.g. "多頭主升段 (均線強勢多頭排列)"
    stage_transition: str  # e.g. "Stage 2 續抱主升波"
    stage_description: str
    action_guidance: str
    stage_reasons: List[str] = field(default_factory=list)
    sma50: float = 0.0
    sma150: float = 0.0
    sma200: float = 0.0
    sma200_slope_pct: float = 0.0
    dist_to_52w_high_pct: float = 0.0
    dist_from_52w_low_pct: float = 0.0
    is_ma_bullish: bool = False  # 50 > 150 > 200
    is_ma_bearish: bool = False  # 50 < 150 < 200


def analyze_market_stage(df: pd.DataFrame) -> StageAnalysisResult:
    """分析個股當前所處的市場階段 (Stage 1 ~ 4).

    採用階層式結構化規則評估均線排列、年線斜率與 52 週週期位置，
    精確區隔四階段並提供量化依據與操作指導方針。

    Args:
        df: 包含 OHLCV 價格之 DataFrame (需至少 252 交易日)

    Returns:
        StageAnalysisResult: 包含所屬階段、均線指標、判讀理由與操作指引之結果物件
    """
    if df.empty or len(df) < 252:
        return StageAnalysisResult(
            stage=0,
            stage_name="STAGE 0 (數據不足無法判定)",
            stage_sub_status="數據不足",
            stage_transition="數據不足",
            stage_description="歷史交易數據不足 252 日，無法精確計算年線 (200MA) 與 52 週週期指標。",
            action_guidance="建議累積至少 1 年歷史交易數據後再行分析。",
            stage_reasons=["歷史資料少於 252 個交易日"],
            sma50=0.0,
            sma150=0.0,
            sma200=0.0,
            sma200_slope_pct=0.0,
            dist_to_52w_high_pct=0.0,
            dist_from_52w_low_pct=0.0,
            is_ma_bullish=False,
            is_ma_bearish=False,
        )

    price_col = (
        "Adj Close"
        if "Adj Close" in df.columns
        else ("Close" if "Close" in df.columns else "close")
    )

    close_series = df[price_col].astype(float)
    sma50 = close_series.rolling(50).mean()
    sma150 = close_series.rolling(150).mean()
    sma200 = close_series.rolling(200).mean()

    high_52w = (
        df["High"].rolling(252).max()
        if "High" in df.columns
        else df["high"].rolling(252).max()
    )
    low_52w = (
        df["Low"].rolling(252).min()
        if "Low" in df.columns
        else df["low"].rolling(252).min()
    )

    cur_close = float(close_series.iloc[-1])
    cur_sma50 = float(sma50.iloc[-1])
    cur_sma150 = float(sma150.iloc[-1])
    cur_sma200 = float(sma200.iloc[-1])
    cur_high_52w = float(high_52w.iloc[-1])
    cur_low_52w = float(low_52w.iloc[-1])

    # 計算 200MA 近 1 個月 (22 交易日) 斜率
    past_sma200 = (
        float(sma200.iloc[-23]) if len(sma200) >= 23 else cur_sma200
    )
    sma200_slope_pct = (
        ((cur_sma200 - past_sma200) / past_sma200 * 100.0)
        if past_sma200 > 0
        else 0.0
    )

    # 距離 52 週高低點百分比
    dist_to_52w_high_pct = (
        ((cur_close - cur_high_52w) / cur_high_52w * 100.0)
        if cur_high_52w > 0
        else 0.0
    )
    dist_from_52w_low_pct = (
        ((cur_close - cur_low_52w) / cur_low_52w * 100.0)
        if cur_low_52w > 0
        else 0.0
    )

    # 均線排列特徵
    is_ma_bullish = bool(cur_sma50 > cur_sma150 > cur_sma200)
    is_ma_bearish = bool(cur_sma50 < cur_sma150 < cur_sma200)

    # ── 階層式四階段精準判定邏輯 ──
    stage = 1
    stage_name = ""
    stage_sub_status = ""
    stage_transition = ""
    stage_description = ""
    action_guidance = ""
    reasons: List[str] = []

    # 1. 【STAGE 2: 上升多頭階段 (Mark-Up)】
    # 核心條件：股價維持在 200MA 之上 (或剛站上/回測年線)，年線不呈下彎趨勢，中長線均線向上擴張
    if (
        cur_close >= cur_sma200 * 0.98
        and sma200_slope_pct >= -0.25
        and (cur_sma150 >= cur_sma200 or cur_sma50 >= cur_sma200)
        and dist_from_52w_low_pct >= 20.0
        and dist_to_52w_high_pct >= -35.0
    ):
        stage = 2
        stage_name = "STAGE 2 (強勢上升階段 / Mark-Up)"

        if is_ma_bullish and cur_close > cur_sma50 and dist_to_52w_high_pct >= -18.0:
            stage_sub_status = "🌟 標準多頭主升段"
            stage_transition = "Stage 2 主升強勢波段"
            stage_description = "均線呈現標準多頭排列，股價站穩各期均線之上且緊貼 52 週高點，主力籌碼鎖定良好。"
            action_guidance = "🔥 核心進場階段！密切注意 VCP 波動收斂後帶量突破樞紐點 (Pivot Point) 的買進機會。"
        elif cur_close < cur_sma50:
            stage_sub_status = "📈 多頭拉回整理段"
            stage_transition = "Stage 2 季線/半年線測試"
            stage_description = "年線與半年線中長多格局未變，短線回檔跌破 50MA 進行籌碼洗盤整理，下檔守穩 150MA/200MA。"
            action_guidance = "👀 觀察拉回整理！等待量縮止跌並形成緊密收斂基底，切勿逆勢盲目搶反彈。"
        else:
            stage_sub_status = "📈 多頭震盪推升段"
            stage_transition = "Stage 2 穩健推升"
            stage_description = "股價穩居年線與半年線之上，年線持續向上揚升，具備持續墊高的多頭特徵。"
            action_guidance = "🎯 掌握突破樞紐！列入觀察名單，鎖定帶量突破頸線的加碼機會。"

        reasons.append(f"股價 ({cur_close:,.2f}) 守穩年線 (200MA: {cur_sma200:,.2f}) 之上")
        reasons.append(f"200MA 年線斜率呈上升趨勢 (+{sma200_slope_pct:.2f}%)")
        reasons.append(f"遠離 52 週低點達 +{dist_from_52w_low_pct:.1f}%，且距高點在 -35% 範圍內")

    # 2. 【STAGE 3: 高檔頭部階段 (Distribution)】
    # 核心條件：曾有大波段漲幅 (距低點 >= 30%)，近期跌破 50MA 且距高點拉回達 -15% 以上，但年線尚未持續下彎 (斜率 >= -0.2%)
    elif (
        dist_from_52w_low_pct >= 30.0
        and dist_to_52w_high_pct <= -15.0
        and (cur_close < cur_sma50 or cur_close < cur_sma150)
        and sma200_slope_pct >= -0.25
    ):
        stage = 3
        stage_name = "STAGE 3 (高檔頭部階段 / Distribution)"

        if cur_close < cur_sma150 or cur_close < cur_sma200:
            stage_sub_status = "⚠️ 頭部出貨破線"
            stage_transition = "Stage 3 頭部成形，瀕臨轉入 Stage 4 危機"
            stage_description = "股價自高點大跌並相繼跌破季線與半年線/年線，高檔套牢賣壓沉重，籌碼明顯渙散出逃。"
            action_guidance = "⚠️ 逢高出脫！頭部型態已成形，反彈即為最後出場點，切勿追高或留戀。"
        else:
            stage_sub_status = "⚠️ 高檔震盪盤頭"
            stage_transition = "Stage 2 轉入 Stage 3 做頭警訊"
            stage_description = "經歷波段大漲後在高檔劇烈震盪，跌破 50MA，量價呈現滯漲或巨量長黑等主力出貨徵兆。"
            action_guidance = "⚠️ 分批停利！收緊移動停損線，不宜進行任何突破追價買進操作。"

        reasons.append(f"自 52 週低點累積漲幅達 +{dist_from_52w_low_pct:.1f}%，高檔獲利回吐賣壓沉重")
        reasons.append(f"跌破 50MA (季線: {cur_sma50:,.2f})，短期多頭動能喪失")
        reasons.append(f"距 52 週高點拉回達 {dist_to_52w_high_pct:.1f}%，高檔做頭跡象明顯")

    # 3. 【STAGE 4: 下跌空頭階段 (Mark-Down)】
    # 核心條件：股價跌破年線 (200MA)，且具備明確空頭特徵 (年線下彎 / 均線空頭排列 / 大幅破底)
    elif cur_close < cur_sma200:
        bearish_signals = []
        if sma200_slope_pct < 0.0:
            bearish_signals.append("年線下彎")
        if cur_sma150 < cur_sma200:
            bearish_signals.append("半年線跌破年線")
        if cur_sma50 < cur_sma150:
            bearish_signals.append("季線跌破半年線")
        if dist_to_52w_high_pct < -25.0:
            bearish_signals.append(f"距高點跌幅達 {dist_to_52w_high_pct:.1f}%")
        if dist_from_52w_low_pct < 25.0:
            bearish_signals.append("持續破底貼近 52 週新低")

        # 若滿足 2 個以上空頭訊號，或股價深跌於年線下方超過 5%
        if len(bearish_signals) >= 2 or cur_close < cur_sma200 * 0.95:
            stage = 4
            stage_name = "STAGE 4 (下跌空頭階段 / Mark-Down)"

            if is_ma_bearish and dist_from_52w_low_pct < 15.0:
                stage_sub_status = "⛔ 空頭主跌段"
                stage_transition = "Stage 4 破底探底進行中"
                stage_description = "均線呈現標準空頭排列 (50 < 150 < 200)，年線陡峭下彎，股價一路破底無支撐。"
                action_guidance = "⛔ 徹底迴避！此階段任何短線反彈多為逃命波，嚴禁進場抄底或向下攤平。"
            elif cur_close > cur_sma50:
                stage_sub_status = "⚠️ 空頭弱勢反彈段"
                stage_transition = "Stage 4 跌深反彈遇壓"
                stage_description = "股價跌深後站回短期 50MA，但上方 150MA 與 200MA 均線沉重反壓下彎，空方趨勢未變。"
                action_guidance = "⚠️ 嚴控風險！空頭反彈不可視為回升，持股者宜逢反彈減碼出清。"
            else:
                stage_sub_status = "⛔ 空頭盤跌探底段"
                stage_transition = "Stage 4 弱勢探底"
                stage_description = "股價在年線下方反覆破底盤跌，年線持續下彎，空方全面掌控盤勢。"
                action_guidance = "⛔ 保留資金！不可買進，耐心等待漫長的築底期展開。"

            reasons.append(f"股價 ({cur_close:,.2f}) 跌破年線 (200MA: {cur_sma200:,.2f})")
            reasons.extend(bearish_signals[:3])
        else:
            # 跌破年線但尚未構成完整空頭排列（初跌或震盪跌破）
            stage = 4
            stage_name = "STAGE 4 (初跌轉空階段 / Breakdown)"
            stage_sub_status = "⚠️ 跌破年線轉弱"
            stage_transition = "Stage 3 轉入 Stage 4"
            stage_description = "股價跌破年線防線，中期均線開始轉折下彎，多頭結構遭到破壞。"
            action_guidance = "⚠️ 嚴格停損！跌破年線通常意味著大波段行情的終結，不可心存僥倖。"
            reasons.append(f"股價跌破年線 (200MA: {cur_sma200:,.2f})，多頭防線失守")

    # 4. 【STAGE 1: 打底築底階段 (Accumulation)】
    # 核心條件：低檔橫盤整理、經歷長期下跌後跌勢趨緩、200MA 走平、均線糾結收斂
    else:
        stage = 1
        stage_name = "STAGE 1 (打底築底階段 / Accumulation)"

        if cur_close >= cur_sma200 and cur_sma50 >= cur_sma150:
            stage_sub_status = "👀 底部轉強發動段"
            stage_transition = "Stage 1 蓄勢轉入 Stage 2"
            stage_description = "股價在底部整理後重新站上走平的年線，短期均線由下往上穿越，具備轉入 Stage 2 的潛力。"
            action_guidance = "🔍 密切追蹤！觀察底部是否出現收縮結構，等待帶量突破頸線確認進入 Stage 2。"
        else:
            stage_sub_status = "👀 底部橫盤整理段"
            stage_transition = "Stage 1 長期打底籌碼沉澱"
            stage_description = "股價在低檔區間狹幅震盪，均線糾結走平，成交量極度萎縮，正在進行漫長的籌碼換手與洗盤。"
            action_guidance = "⏳ 耐心等待！列入觀察名單即可，不可過早進場消耗時間成本。"

        reasons.append("股價在低檔區間震盪整理，尚未展開明確多頭或空頭方向")
        reasons.append(f"年線走平糾結 (200MA 斜率: {sma200_slope_pct:+.2f}%)，多空處於均勢")

    return StageAnalysisResult(
        stage=stage,
        stage_name=stage_name,
        stage_sub_status=stage_sub_status,
        stage_transition=stage_transition,
        stage_description=stage_description,
        action_guidance=action_guidance,
        stage_reasons=reasons,
        sma50=round(cur_sma50, 2),
        sma150=round(cur_sma150, 2),
        sma200=round(cur_sma200, 2),
        sma200_slope_pct=round(sma200_slope_pct, 2),
        dist_to_52w_high_pct=round(dist_to_52w_high_pct, 2),
        dist_from_52w_low_pct=round(dist_from_52w_low_pct, 2),
        is_ma_bullish=is_ma_bullish,
        is_ma_bearish=is_ma_bearish,
    )
