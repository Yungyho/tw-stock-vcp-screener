"""Telegram 診斷報告格式化器 (Telegram Diagnostic Report Formatter).

將個股分析結果格式化為適合 Telegram 發送的 Markdown 文字訊息。
"""

from typing import Any, Dict, Optional

from src.stage_analyzer import StageAnalysisResult


def format_stock_diagnostic(result: Dict[str, Any]) -> str:
    """將個股分析結果格式化為 Telegram Markdown 文字訊息.

    Args:
        result: analyzer_core.analyze_stock() 回傳的結構化結果字典

    Returns:
        str: 已格式化的 Telegram 訊息字串
    """
    stock_info = result["stock_info"]
    stage_res: StageAnalysisResult = result["stage_res"]
    tt_result = result["tt_result"]
    vcp_result = result["vcp_result"]
    score = result["score"]
    beta_1y = result.get("beta_1y")
    market_cap = result.get("market_cap", 0.0)
    turnover_twd = result.get("turnover_twd", 0.0)
    df = result["df"]

    stock_id = stock_info.get("stock_id", "N/A")
    name = stock_info.get("name", "N/A")
    market = stock_info.get("market", "")
    market_label = "上市" if market == "listed" else ("上櫃" if market == "otc" else market)

    # 價格資訊
    price_col = "close" if "close" in df.columns else "Close"
    date_col = "date" if "date" in df.columns else "Date"
    cur_close = float(df[price_col].iloc[-1]) if not df.empty else 0.0
    latest_date = str(df[date_col].iloc[-1])[:10] if not df.empty else "N/A"

    # 成交量
    vol_col = "volume" if "volume" in df.columns else ("Volume" if "Volume" in df.columns else None)
    avg_vol_20 = float(df[vol_col].tail(20).mean()) if vol_col and len(df) >= 20 else 0.0

    # 格式化市值
    if market_cap >= 1e12:
        mcap_str = f"{market_cap / 1e12:.2f}兆"
    elif market_cap >= 1e8:
        mcap_str = f"{market_cap / 1e8:.1f}億"
    elif market_cap > 0:
        mcap_str = f"{market_cap / 1e4:.0f}萬"
    else:
        mcap_str = "N/A"

    # 格式化成交金額
    if turnover_twd >= 1e8:
        turnover_str = f"{turnover_twd / 1e8:.2f}億"
    elif turnover_twd > 0:
        turnover_str = f"{turnover_twd / 1e4:.0f}萬"
    else:
        turnover_str = "N/A"

    beta_str = f"{beta_1y:.2f}" if beta_1y is not None else "N/A"

    # 階段 emoji
    stage_icons = {1: "👀", 2: "🌟", 3: "⚠️", 4: "⛔"}
    stage_icon = stage_icons.get(stage_res.stage, "❓")

    yahoo_url = f"https://tw.stock.yahoo.com/quote/{stock_id}/technical-analysis"

    lines = []
    lines.append(f"🔍 *{stock_id} {name}* [{market_label}]")
    lines.append(f"━━━━━━━━━━━━━━━━━━")
    lines.append(f"💰 收盤: {cur_close:,.2f} | 📅 {latest_date}")
    lines.append(f"🏢 市值: {mcap_str} | 💵 均金額: {turnover_str}")
    benchmark_name = result.get("benchmark_name", "TAIEX" if market == "listed" else "TPEx")
    lines.append(f"📊 均量: {avg_vol_20:,.0f}張 | ⚡ Beta(vs {benchmark_name}): {beta_str}")
    lines.append("")

    # ── 處置與注意警示 ──
    disposition_info = result.get("disposition_info")
    attention_info = result.get("attention_info")
    if disposition_info:
        interval = disposition_info.get("matching_interval", "分盤撮合")
        rem = disposition_info.get("remaining_trading_days", 0)
        end_d = disposition_info.get("end_date", "")
        soon = " 🚀 *即將出關*" if disposition_info.get("is_exiting_soon") else ""
        lines.append(f"🚨 *【處置股票】* `{interval}` | 剩餘 `{rem}` 日 (至 {end_d}){soon}")
        lines.append(f"   ⚠️ 限制: 禁現股當沖 / 預收款券 (量縮受管制影響)")
        lines.append("")
    elif attention_info:
        notice_d = attention_info.get("notice_date", "")
        lines.append(f"⚠️ *【注意股票】* 公告日: `{notice_d}`")
        lines.append("")

    # ── 階段判定 ──
    lines.append(f"{stage_icon} *階段:* {stage_res.stage_name}")
    if hasattr(stage_res, "stage_sub_status") and stage_res.stage_sub_status:
        lines.append(f"   狀態: `{stage_res.stage_sub_status}` ({stage_res.stage_transition})")
    lines.append(f"   50MA={stage_res.sma50:,.1f} | 150MA={stage_res.sma150:,.1f} | 200MA={stage_res.sma200:,.1f}")
    lines.append(f"   200MA斜率: `{stage_res.sma200_slope_pct:+.2f}%` | 距高點: `{stage_res.dist_to_52w_high_pct:+.1f}%`")
    if hasattr(stage_res, "stage_reasons") and stage_res.stage_reasons:
        lines.append(f"   依據: {stage_res.stage_reasons[0]}")
    lines.append("")

    # ── Trend Template ──
    tt_pass_count = tt_result.get("score", 0)
    core_pass = tt_result.get("is_stage_2_core", False)
    core_label = "✅" if core_pass else "❌"
    lines.append(f"📈 *TT 趨勢模板:* {tt_pass_count}/9 通過 (核心多頭: {core_label})")

    conditions = tt_result.get("conditions", {})
    tt_labels = [
        ("close > SMA150", "股價>150MA"),
        ("close > SMA200", "股價>200MA"),
        ("SMA150 > SMA200", "150MA>200MA"),
        ("SMA200_rising", "200MA上升"),
        ("SMA50 > SMA150", "50MA>150MA"),
        ("SMA50 > SMA200", "50MA>200MA"),
        ("close > SMA50", "股價>50MA"),
        ("above_52w_low_by_25%", "距低點≥25%"),
        ("within_25%_of_52w_high", "距高點≤25%"),
    ]

    for key, label in tt_labels:
        cond = conditions.get(key, {})
        passed = cond.get("passed", False)
        mark = "✅" if passed else "❌"
        lines.append(f"   {mark} {label}")
    lines.append("")

    # ── VCP 型態 ──
    is_vcp = vcp_result.get("is_vcp", False)
    vcp_label = "✅ 符合標準VCP" if is_vcp else "❌ 未達標準VCP"
    tightness = vcp_result.get("tightness", 0.0)
    pivot = vcp_result.get("pivot_price", cur_close)
    distance = vcp_result.get("distance_to_pivot", 0.0)
    contractions = vcp_result.get("contractions", [])
    contraction_details = vcp_result.get("contraction_details", [])
    base_depth = vcp_result.get("base_depth_pct", 0.0)
    failure_reasons = vcp_result.get("failure_reasons", [])

    action_stage = vcp_result.get("action_stage", "UNCONFIRMED")
    stage_tags = {
        "BUY_READY": "🎯買點警戒",
        "RECENT_BREAKOUT": "🚀突破發動",
        "PIVOT_RETEST": "🔄回踩樞紐",
        "EXTENDED": "⚠️過度延伸",
        "FORMING": "⏳基底收縮中",
        "UNCONFIRMED": "❌未確認",
    }
    stage_tag = stage_tags.get(action_stage, action_stage)

    lines.append(f"🌪️ *VCP 型態:* {vcp_label} (`{stage_tag}` | 收縮: `{tightness:.1f}%` | 評分: `{score:.0f}`)")
    lines.append(f"   🎯 Pivot: `{pivot:,.2f}` | 距突破: `{distance:+.1f}%`")
    lines.append(f"   📉 基底最大回檔: `{base_depth:.1f}%` ({vcp_result.get('base_days', 0)}天整理)")

    if contraction_details:
        prog_parts = [f"T{c['seq']}:-{c['depth_pct']:.1f}%({c['bars']}天)" for c in contraction_details]
        lines.append(f"   收縮波次: {' ➜ '.join(prog_parts)}")
    elif contractions:
        lines.append(f"   收縮進程: {' ➜ '.join(f'-{c:.1f}%' for c in contractions)}")
    else:
        lines.append("   收縮進程: 無交替波段收縮 (單邊走勢)")

    is_converging = vcp_result.get("is_converging", False)
    higher_lows = vcp_result.get("higher_lows", False)
    vol_dec = vcp_result.get("volume_declining", False)

    lines.append(f"   收斂: {'✅' if is_converging else '❌'} | 底底高: {'✅' if higher_lows else '❌'} | 量縮: {'✅' if vol_dec else '❌'}")
    if not is_vcp and failure_reasons:
        lines.append(f"   ⚠️ 未達標: {failure_reasons[0]}")
    lines.append("")

    # ── 操作建議 ──
    lines.append("💡 *操作建議:*")
    if stage_res.stage == 2:
        if action_stage == "RECENT_BREAKOUT":
            lines.append(f"   🚀 突破發動初期！近1~3日帶量突破 `{pivot:,.0f}` 元")
            lines.append(f"   若延伸在 +5% 內可小部位試單，超過 +5% 勿追高")
        elif action_stage == "PIVOT_RETEST":
            lines.append(f"   🔄 回踩樞紐有守！突破後量縮回測 `{pivot:,.0f}` 元頸線未破")
            lines.append(f"   為經典二次上車機會，停損設於頸線下方 -3%~-5%")
        elif action_stage == "EXTENDED":
            lines.append(f"   ⚠️ 漲幅過度延伸！距 Pivot 已脫離 `{abs(distance):.1f}%` (>+8%)")
            lines.append(f"   嚴禁高檔追價，等待拉回整理構築新基底")
        elif is_vcp:
            if 0 <= distance <= 5.0:
                lines.append(f"   🎯 最佳買點在即！關注 `{pivot:,.0f}` 元帶量突破")
                lines.append(f"   停損建議: `{pivot * 0.93:,.0f}` 元 (-7%)")
            elif distance < 0:
                lines.append(f"   🚀 已突破 Pivot `{pivot:,.0f}` 元，注意勿追高超過 +5%")
            else:
                lines.append(f"   ⏳ Stage 2 + VCP 收斂中，距突破 `{distance:+.1f}%`，列入自選追蹤")
        else:
            if len(contractions) == 1:
                lines.append(f"   ⏳ Stage 2 初次回檔中 (1T: -{contractions[0]:.1f}%)，待第2波收斂")
            elif len(contractions) >= 2:
                lines.append(f"   📈 Stage 2 多頭，VCP 尚未收緊，等待整理築底")
            else:
                lines.append("   📈 Stage 2 多頭推升，尚未出現完整收縮基底")
    elif stage_res.stage == 4:
        lines.append("   ⛔ Stage 4 空頭下跌波段，任何反彈多為中繼，嚴禁買進或攤平")
    elif stage_res.stage == 3:
        lines.append("   ⚠️ Stage 3 高檔頭部震盪，籌碼鬆動，建議分批減碼防守")
    else:
        # Stage 1
        if cur_close >= stage_res.sma200:
            lines.append("   👀 Stage 1 底部轉強段，站上年線，觀察帶量突破轉 Stage 2")
        else:
            lines.append("   👀 Stage 1 長期打底整理，耐心等待形態成型")

    lines.append(f"\n🔗 [開啟雅虎技術分析圖]({yahoo_url})")

    return "\n".join(lines)
