"""終端機診斷報告輸出器 (Console Diagnostic Reporter).

將個股之四階段分析、Trend Template 9 項條件、VCP 逐波收縮進程與流動性指標
格式化渲染為美觀整齊的終端機 ASCII 報表。
支援 Windows UTF-8 自動相容編碼。
"""

import sys
from typing import Any, Dict, Optional
import pandas as pd

from src.stage_analyzer import StageAnalysisResult

# 確保 Windows 終端機能正常輸出 Unicode / Emoji
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def print_stock_diagnostic_report(
    stock_info: Dict[str, Any],
    df: pd.DataFrame,
    stage_res: StageAnalysisResult,
    tt_result: Dict[str, Any],
    vcp_result: Dict[str, Any],
    score: float,
    beta_1y: Optional[float] = None,
    market_cap: Optional[float] = None,
    turnover_twd: Optional[float] = None,
    benchmark_name: Optional[str] = None,
    disposition_info: Optional[Dict[str, Any]] = None,
    attention_info: Optional[Dict[str, Any]] = None,
) -> None:
    """輸出單檔股票的完整技術面與階段診斷報告至 Console."""
    sep = "=" * 88
    sub_sep = "-" * 88

    stock_id = stock_info.get("stock_id", "N/A")
    name = stock_info.get("name", "N/A")
    market = stock_info.get("market", "")
    market_label = "上市 (TWSE)" if market == "listed" else ("上櫃 (TPEx)" if market == "otc" else market)

    date_col = "date" if "date" in df.columns else "Date"
    price_col = "Close" if "Close" in df.columns else ("close" if "close" in df.columns else df.columns[0])
    latest_date = str(df[date_col].iloc[-1])[:10] if not df.empty else "N/A"
    cur_close = float(df[price_col].iloc[-1]) if not df.empty else 0.0

    # 成交量與成交金額
    vol_col = "Volume" if "Volume" in df.columns else ("volume" if "volume" in df.columns else None)
    if vol_col and len(df) >= 20:
        avg_vol_20 = float(df[vol_col].tail(20).mean())
        calc_turnover = avg_vol_20 * 1000.0 * cur_close
    else:
        avg_vol_20 = 0.0
        calc_turnover = 0.0

    disp_turnover = turnover_twd if turnover_twd and turnover_twd > 0 else calc_turnover
    disp_mcap = market_cap if market_cap and market_cap > 0 else 0.0

    # 格式化文字
    if disp_mcap >= 1e12:
        mcap_str = f"{disp_mcap / 1e12:.2f} 兆 TWD"
    elif disp_mcap >= 1e8:
        mcap_str = f"{disp_mcap / 1e8:.1f} 億 TWD"
    elif disp_mcap > 0:
        mcap_str = f"{disp_mcap / 1e4:.0f} 萬 TWD"
    else:
        mcap_str = "資料庫未載入"

    if disp_turnover >= 1e8:
        turnover_str = f"{disp_turnover / 1e8:.2f} 億 TWD"
    elif disp_turnover > 0:
        turnover_str = f"{disp_turnover / 1e4:.0f} 萬 TWD"
    else:
        turnover_str = "N/A"

    beta_str = f"{beta_1y:.2f}" if beta_1y is not None else "N/A"

    print(f"\n{sep}")
    print(f" 🔍 台股技術型態與階段診斷報告: {stock_id} {name} [{market_label}]")
    print(f"{sep}")
    print(f" 💰 最新收盤價: {cur_close:,.2f} 元 | 📅 日期: {latest_date}")
    print(f" 🏢 總市值    : {mcap_str} | 💵 20日均成交金額: {turnover_str}")
    bench_label = benchmark_name or ("TAIEX" if stock_info.get("market") != "otc" else "TPEx")
    print(f" 📊 20日均量  : {avg_vol_20:,.0f} 張 | ⚡ 1年期 Beta (vs {bench_label}): {beta_str}")
    print(f"{sub_sep}")

    # ── 0. 處置與注意警示 ──
    if disposition_info:
        interval = disposition_info.get("matching_interval", "分盤撮合")
        rem_days = disposition_info.get("remaining_trading_days", 0)
        start_d = disposition_info.get("start_date", "")
        end_d = disposition_info.get("end_date", "")
        disp_type = disposition_info.get("disposition_type", "處置股票")
        reasons = disposition_info.get("reasons", "")
        soon_mark = " 🚀 [即將出關 / 剩餘 <= 2天]" if disposition_info.get("is_exiting_soon") else ""
        print(f" 🚨 【處置股票警示 (Disposition Alert)】: {disp_type} ({interval}){soon_mark}")
        print(f"    • 處置期間  : {start_d} ~ {end_d} (剩餘 {rem_days} 個營業日)")
        if reasons:
            print(f"    • 處置原因  : {reasons}")
        print(f"    • 交易限制  : ⚠️ 全面禁止現股當沖 / 部分或全部款券預收圈存")
        print(f"    • VCP 注意  : 處置期間日均量斷崖式萎縮為法規限制所致，需重點觀察股價是否在低量下抗跌橫盤或墊高底底高")
        print(f"{sub_sep}")
    elif attention_info:
        notice_d = attention_info.get("notice_date", "")
        reasons = attention_info.get("reasons", "")
        print(f" ⚠️ 【注意股票資訊 (Attention Stock)】: 公告日期 {notice_d}")
        if reasons:
            print(f"    • 注意理由  : {reasons[:80]}...")
        print(f"{sub_sep}")

    # ── 1. 市場階段判定 ──
    stage_icons = {1: "👀", 2: "🌟", 3: "⚠️", 4: "⛔"}
    icon = stage_icons.get(stage_res.stage, "❓")
    print(f" 🎯 【市場階段判定 (Market Stage)】: {icon} {stage_res.stage_name}")
    if hasattr(stage_res, "stage_sub_status") and stage_res.stage_sub_status:
        print(f"    • 次階段狀態: {stage_res.stage_sub_status} (轉換訊號: {stage_res.stage_transition})")
    print(f"    • 階段特徵  : {stage_res.stage_description}")
    print(f"    • 均線結構  : 50MA={stage_res.sma50:,.2f} | 150MA={stage_res.sma150:,.2f} | 200MA={stage_res.sma200:,.2f} (200MA斜率: {stage_res.sma200_slope_pct:+.2f}%)")
    print(f"    • 週期位置  : 距52週高點 {stage_res.dist_to_52w_high_pct:+.1f}% | 距52週低點 {stage_res.dist_from_52w_low_pct:+.1f}%")
    if hasattr(stage_res, "stage_reasons") and stage_res.stage_reasons:
        print(f"    • 判定依據  : {'；'.join(stage_res.stage_reasons)}")
    print(f"    • 操作方針  : {stage_res.action_guidance}")
    print(f"{sub_sep}")

    # ── 2. Trend Template 9 條件 ──
    tt_pass_count = tt_result.get("score", 0)
    core_pass = tt_result.get("is_stage_2_core", False)
    core_label = "✅ 通過 (符合核心多頭)" if core_pass else "❌ 未達標 (非健康多頭)"
    print(f" 📈 【Trend Template 趨勢模板檢測】: 通過 {tt_pass_count}/9 項條件 (Stage 2 核心: {core_label})")

    conditions = tt_result.get("conditions", {})
    tt_labels = [
        ("close > SMA150", "1. 股價高於 150MA (半年線)"),
        ("close > SMA200", "2. 股價高於 200MA (年線)"),
        ("SMA150 > SMA200", "3. 150MA 高於 200MA (中長線多頭)"),
        ("SMA200_rising", "4. 200MA 長期趨勢向上 (斜率正向)"),
        ("SMA50 > SMA150", "5. 50MA 高於 150MA"),
        ("SMA50 > SMA200", "6. 50MA 高於 200MA"),
        ("close > SMA50", "7. 股價高於 50MA (季線)"),
        ("above_52w_low_by_25%", "8. 距離 52 週最低點 ≥ +25%"),
        ("within_25%_of_52w_high", "9. 距離 52 週最高點在 25% 內"),
    ]

    for key, label in tt_labels:
        cond_data = conditions.get(key, {})
        passed = cond_data.get("passed", False)
        mark = "[✅]" if passed else "[❌]"
        val = cond_data.get("value", 0.0)
        thresh = cond_data.get("threshold", 0.0)

        if key == "SMA200_rising":
            val_str = f"現200MA: {val:,.2f} >= 前月200MA: {thresh:,.2f}"
        elif "52w" in key:
            val_str = f"現價: {val:,.2f} vs 門檻: {thresh:,.2f}"
        else:
            val_str = f"{val:,.2f} {'>' if passed else '<='} {thresh:,.2f}"

        print(f"    {mark} {label:<32}: {val_str}")
    print(f"{sub_sep}")

    # ── 3. VCP 波動收斂檢測 (無論是否符合 VCP，均完整輸出基底與收縮診斷) ──
    is_vcp = vcp_result.get("is_vcp", False)
    vcp_label = "✅ 符合標準 VCP 型態" if is_vcp else "❌ 未達標準 VCP 型態"
    tightness = vcp_result.get("tightness", 0.0)
    pivot = vcp_result.get("pivot_price", cur_close)
    distance = vcp_result.get("distance_to_pivot", 0.0)
    contractions = vcp_result.get("contractions", [])
    contraction_details = vcp_result.get("contraction_details", [])
    base_depth = vcp_result.get("base_depth_pct", 0.0)
    base_high = vcp_result.get("base_high", pivot)
    base_low = vcp_result.get("base_low", 0.0)
    base_days = vcp_result.get("base_days", 0)
    checklist = vcp_result.get("checklist", [])
    failure_reasons = vcp_result.get("failure_reasons", [])

    action_stage = vcp_result.get("action_stage", "UNCONFIRMED")
    stage_tags = {
        "BUY_READY": "🎯 買點警戒 (In Base)",
        "RECENT_BREAKOUT": "🚀 突破發動初期 (1~3日)",
        "PIVOT_RETEST": "🔄 回踩樞紐有守 (Retest)",
        "EXTENDED": "⚠️ 漲幅過度延伸 (Extended)",
        "FORMING": "⏳ 基底整理收縮中 (Forming)",
        "UNCONFIRMED": "❌ 形態未成型",
    }
    stage_tag = stage_tags.get(action_stage, action_stage)

    print(f" 🌪️ 【VCP 波動收斂型態檢測】: {vcp_label} (型態進程: {stage_tag} | 收縮度: {tightness:.1f}% | 總評分: {score:.1f} 分)")
    print(f"    • 基底樞紐高點 (Pivot)    : {pivot:,.2f} 元 (當前距突破點: {distance:+.2f}%)")
    print(f"    • 基底區間與最大深度      : {base_depth:.1f}% (最高: {base_high:,.2f} | 最低: {base_low:,.2f} | 整理: {base_days}天)")
    print(f"    • 收縮波次進程 ({len(contractions)}T)      : {' ➜ '.join(f'-{c:.1f}%' for c in contractions) if contractions else '無交替收縮波段 (單邊走勢)'}")

    if contraction_details:
        prog_parts = [f"T{c['seq']}: -{c['depth_pct']:.1f}% ({c['bars']}天, 低:{c.get('low_price', 0):,.1f})" for c in contraction_details]
        print(f"    • 收縮波次詳細明細        : {' ➜ '.join(prog_parts)}")

    if checklist:
        print(f"    • VCP 關鍵要素檢核清單    :")
        for item in checklist:
            mark = "[✅]" if item["passed"] else "[❌]"
            print(f"      {mark} {item['name']:<28}: {item['detail']}")

    if not is_vcp and failure_reasons:
        print(f"    • ⚠️ 未達標原因診斷分析   : {'；'.join(failure_reasons)}")
    print(f"{sub_sep}")

    # ── 4. 綜合操作建議 (分階段與 VCP 型態客製化) ──
    print(" 💡 【綜合操作建議 (Minervini SEPA 指引)】:")
    if stage_res.stage == 2:
        if action_stage == "RECENT_BREAKOUT":
            print(f"    🚀 【突破發動初期】：該股於近 1~3 日內帶量突破 Pivot {pivot:,.2f} 元 (當前距樞紐 {distance:+.1f}%)。")
            print(f"       若尚未持有且延伸幅度在 +5% 內可考慮小部位試單；若已超過 +5% 則切勿追高，等待拉回測試。")
        elif action_stage == "PIVOT_RETEST":
            print(f"    🔄 【回踩樞紐有守】：突破後量縮回測 Pivot {pivot:,.2f} 元頸線支撐未破 (當前距樞紐 {distance:+.1f}%)。")
            print(f"       此為 Minervini 經典之二次進場良機，停損建議設於頸線下方 -3% ~ -5%。")
        elif action_stage == "EXTENDED":
            print(f"    ⚠️ 【漲幅過度延伸警告】：當前價位已大幅脫離 Pivot {pivot:,.2f} 元 (目前延伸 {abs(distance):.1f}%，超過 +8% 買點上限)。")
            print(f"       此位置追高極易遭受劇烈回檔洗盤，嚴禁追價！建議等待下一波整理基底成型。")
        elif is_vcp:
            if 0 <= distance <= 5.0:
                print(f"    🎯 【最佳買點在即】：該股處於健康 Stage 2 上升趨勢，VCP 收斂極致 ({tightness:.1f}%)。")
                print(f"       建議關注 {pivot:,.2f} 元樞紐點帶量突破，停損建議設於 {pivot * 0.93:,.2f} 元 (-7%)。")
            elif distance < 0:
                print(f"    🚀 【已突破樞紐點】：股價已站上 Pivot {pivot:,.2f} 元，請注意切勿過度追高超過 +5%，嚴守進場停損。")
            else:
                print(f"    ⏳ 【觀察中】：處於 Stage 2 且已形成收斂，距突破點尚有 {distance:.1f}%，建議列入自選股等待收縮至最後一波。")
        else:
            if len(contractions) == 1:
                print(f"    ⏳ 處於 Stage 2 多頭回檔中，目前僅完成第 1 波回檔 (1T: -{contractions[0]:.1f}%)，尚未收縮完畢。")
                print(f"       建議等待後續第 2~3 波收縮 (2T~3T) 形成緊密基底，再伺機佈局。")
            elif len(contractions) >= 2:
                reasons_str = "、".join(failure_reasons[:2]) if failure_reasons else "尚未形成緊密對稱形態"
                print(f"    📈 處於 Stage 2 多頭軌道，但目前尚未形成緊密 VCP 型態 ({reasons_str})。")
                print(f"       建議等待拉回整理築底、波段低點不再破底並收斂出緊密波次後再行介入。")
            else:
                print(f"    📈 處於 Stage 2 多頭走勢，近期呈現單邊推升或高檔橫盤，尚未出現完整的波段收縮基底。")
    elif stage_res.stage == 4:
        print("    ⛔ 【空頭波段風險警告】：處於 Stage 4 下跌空頭波段，均線下彎破底。")
        print("       此階段任何形態收斂多為下跌中繼或逃命反彈，嚴禁進場抄底或向下攤平，請保留資金。")
    elif stage_res.stage == 3:
        print("    ⚠️ 【高檔做頭風險警訊】：處於 Stage 3 高檔頭部震盪期，大漲後主力籌碼出現渙散出貨跡象。")
        print("       上方套牢賣壓沉重，不宜追高加碼，持股者應收緊移動停損或分批獲利了結。")
    else:
        # Stage 1
        if cur_close >= stage_res.sma200:
            print("    👀 【底部蓄勢發動觀察】：處於 Stage 1 築底末期，股價站上走平年線。")
            print("       可密切觀察底部是否有收縮蓄勢現象，耐心等待帶量突破年線並轉入 Stage 2 的主升確認點。")
        else:
            print("    👀 【長期底部打底期】：處於 Stage 1 築底打底期，均線糾結橫盤，籌碼換手中。")
            print("       耐心列入觀察清單即可，過早進場容易消耗長時間等待的時間成本。")

    yahoo_url = f"https://tw.stock.yahoo.com/quote/{stock_id}/technical-analysis"
    print(f"\n 🔗 雅虎股市技術圖: {yahoo_url}")
    print(f"{sep}\n")


def print_market_breadth_report(
    summary: Dict[str, Any],
    chart_path: Optional[str] = None,
) -> None:
    """輸出全市場寬度指標摘要至 Console 終端機.

    Args:
        summary: market_breadth.get_market_breadth_summary 回傳的摘要字典
        chart_path: 折線圖實體圖檔路徑 (若有)
    """
    sep = "=" * 88
    sub_sep = "-" * 88

    date_str = summary.get("date", "N/A")
    samples = summary.get("sample_stocks", 0)
    pct_50 = summary.get("pct_50", 0.0)
    chg_50_1d = summary.get("pct_50_chg_1d", 0.0)
    chg_50_5d = summary.get("pct_50_chg_5d", 0.0)

    pct_200 = summary.get("pct_200", 0.0)
    chg_200_1d = summary.get("pct_200_chg_1d", 0.0)
    chg_200_5d = summary.get("pct_200_chg_5d", 0.0)

    regime = summary.get("market_regime", "N/A")
    guidance = summary.get("guidance", "N/A")

    print(f"\n{sep}")
    print(f" 📊 全市場寬度指標 (Market Breadth — 近 1 年 50MA & 200MA 走勢)")
    print(f"{sep}")
    print(f" 📅 統計基準日期: {date_str} (有效樣本個股: {samples:,} 檔)")
    print(f"{sub_sep}")
    print(f" 🟢 股價高於 50MA (季線)  比例: {pct_50:5.1f}%  [前日: {chg_50_1d:+5.1f}% | 5日變動: {chg_50_5d:+5.1f}%]")
    print(f" 🟠 股價高於 200MA (年線) 比例: {pct_200:5.1f}%  [前日: {chg_200_1d:+5.1f}% | 5日變動: {chg_200_5d:+5.1f}%]")
    print(f"{sub_sep}")
    print(f" 🎯 市場結構狀態判定 : {regime}")
    print(f" 💡 操作方針建議     : {guidance}")
    if chart_path:
        print(f" 🖼️ 寬度指標折線圖   : {chart_path}")
    print(f"{sep}\n")
