"""全市場寬度指標計算模組 (Market Breadth Indicator Module).

計算全市場（或特定市場）在最近一年內，每日股價高於 50MA（季線）與 200MA（年線）的股票比例，
提供總體市場健康度、多空擴散力道與潛在頂底部背離的量化依據。
"""

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from src.db.manager import DBManager

logger = logging.getLogger(__name__)


def calculate_market_breadth(
    db: DBManager,
    days: int = 250,
    market: Optional[str] = None,
) -> pd.DataFrame:
    """計算近 1 年全市場每日股價高於 50MA 與 200MA 的股票比例.

    Args:
        db: DBManager 資料庫管理實例
        days: 統計的交易日天數 (預設 250 天，約 1 年)
        market: 市場篩選 ('listed' 僅上市 / 'otc' 僅上櫃 / 'all' 或 None 全市場)

    Returns:
        pd.DataFrame: 以日期為索引，包含以下欄位：
            - pct_above_50ma: 股價高於 50MA 的比例 (%)
            - pct_above_200ma: 股價高於 200MA 的比例 (%)
            - total_stocks_50ma: 有效計算 50MA 的股票檔數
            - total_stocks_200ma: 有效計算 200MA 的股票檔數
            - benchmark_close: 同期 TAIEX 加權指數收盤價 (若有)
    """
    empty_result = pd.DataFrame(
        columns=[
            "pct_above_50ma",
            "pct_above_200ma",
            "total_stocks_50ma",
            "total_stocks_200ma",
            "benchmark_close",
        ]
    )

    try:
        cursor = db.conn.cursor()

        # 1. 取得近 `days` 個交易日的日期列表
        cursor.execute(
            "SELECT date FROM price_history GROUP BY date ORDER BY date DESC LIMIT ?",
            (days,),
        )
        dates_1y_rows = cursor.fetchall()
        if not dates_1y_rows:
            logger.warning("資料庫中無足夠的價格日期以計算市場寬度")
            return empty_result

        dates_1y = sorted([r[0] for r in dates_1y_rows])
        start_date_1y = dates_1y[0]
        latest_date = dates_1y[-1]

        # 2. 往前再抓 300 個交易日，以便首日能充足覆蓋 200MA 所需窗口 (防範停牌或缺日)
        cursor.execute(
            "SELECT date FROM price_history WHERE date < ? GROUP BY date ORDER BY date DESC LIMIT 300",
            (start_date_1y,),
        )
        extra_dates_rows = cursor.fetchall()
        earliest_date = extra_dates_rows[-1][0] if extra_dates_rows else start_date_1y

        logger.info(
            "計算市場寬度指標: 統計區間 %s ~ %s (歷史預載至 %s, 市場: %s)",
            start_date_1y,
            latest_date,
            earliest_date,
            market or "all",
        )

        # 3. 根據市場設定建立查詢 SQL
        market_clean = (market or "").strip().lower()
        if market_clean in ("listed", "otc"):
            query_sql = """
                SELECT p.date, p.stock_id, p.close
                FROM price_history p
                JOIN stock_list s ON p.stock_id = s.stock_id
                WHERE p.date >= ?
                  AND s.market = ?
                  AND p.stock_id NOT IN ('^TWII', '006201.TWO')
            """
            params = (earliest_date, market_clean)
        else:
            query_sql = """
                SELECT p.date, p.stock_id, p.close
                FROM price_history p
                WHERE p.date >= ?
                  AND p.stock_id NOT IN ('^TWII', '006201.TWO')
            """
            params = (earliest_date,)

        price_df = pd.read_sql_query(query_sql, db.conn, params=params)
        if price_df.empty:
            logger.warning("查詢所得價格數據為空，無法計算市場寬度")
            return empty_result

        # 4. 建立 日期 × 股票代號 的收盤價矩陣
        pivot_prices = price_df.pivot(index="date", columns="stock_id", values="close")

        # 5. 向量化計算 50MA 與 200MA (容許少數因停牌產生的缺日)
        ma50_matrix = pivot_prices.rolling(50, min_periods=40).mean()
        ma200_matrix = pivot_prices.rolling(200, min_periods=160).mean()

        # 6. 截取統計區間 (近 1 年)
        piv_1y = pivot_prices.loc[pivot_prices.index >= start_date_1y]
        ma50_1y = ma50_matrix.loc[ma50_matrix.index >= start_date_1y]
        ma200_1y = ma200_matrix.loc[ma200_matrix.index >= start_date_1y]

        # 7. 計算百分比
        valid_50 = ma50_1y.notna()
        valid_200 = ma200_1y.notna()

        count_valid_50 = valid_50.sum(axis=1)
        count_valid_200 = valid_200.sum(axis=1)

        above_50 = (piv_1y > ma50_1y) & valid_50
        above_200 = (piv_1y > ma200_1y) & valid_200

        pct_50 = (above_50.sum(axis=1) / count_valid_50.replace(0, np.nan)) * 100.0
        pct_200 = (above_200.sum(axis=1) / count_valid_200.replace(0, np.nan)) * 100.0

        breadth_df = pd.DataFrame(
            {
                "pct_above_50ma": pct_50.round(2),
                "pct_above_200ma": pct_200.round(2),
                "total_stocks_50ma": count_valid_50,
                "total_stocks_200ma": count_valid_200,
            },
            index=piv_1y.index,
        )

        # 8. 載入大盤指數 (^TWII) 收盤價對照
        bench_sql = """
            SELECT date, close as benchmark_close
            FROM price_history
            WHERE stock_id = '^TWII' AND date >= ?
            ORDER BY date ASC
        """
        bench_df = pd.read_sql_query(bench_sql, db.conn, params=(start_date_1y,))
        if not bench_df.empty:
            bench_df = bench_df.set_index("date")
            breadth_df = breadth_df.join(bench_df, how="left")
            breadth_df["benchmark_close"] = breadth_df["benchmark_close"].ffill()
        else:
            breadth_df["benchmark_close"] = np.nan

        breadth_df.index.name = "date"
        logger.info(
            "市場寬度指標計算完成: 共 %d 個交易日，最新日 50MA%%=%.1f%%, 200MA%%=%.1f%%",
            len(breadth_df),
            breadth_df["pct_above_50ma"].iloc[-1] if not breadth_df.empty else 0.0,
            breadth_df["pct_above_200ma"].iloc[-1] if not breadth_df.empty else 0.0,
        )
        return breadth_df

    except Exception as e:
        logger.error("計算全市場寬度指標時發生錯誤: %s", e, exc_info=True)
        return empty_result


def get_market_breadth_summary(breadth_df: pd.DataFrame) -> Dict[str, Any]:
    """產出市場寬度最新數據摘要、變動量與盤勢結構解讀.

    Args:
        breadth_df: calculate_market_breadth 回傳的 DataFrame

    Returns:
        Dict[str, Any]: 包含最新數值、變動幅度、多空評語與 Telegram Caption 等
    """
    if breadth_df.empty:
        return {
            "date": "N/A",
            "pct_50": 0.0,
            "pct_200": 0.0,
            "pct_50_chg_1d": 0.0,
            "pct_50_chg_5d": 0.0,
            "pct_200_chg_1d": 0.0,
            "pct_200_chg_5d": 0.0,
            "sample_stocks": 0,
            "market_regime": "數據不足",
            "guidance": "歷史資料不足，無法解讀市場結構",
            "caption": "📊 全市場寬度指標：數據不足",
        }

    latest = breadth_df.iloc[-1]
    prev_1d = breadth_df.iloc[-2] if len(breadth_df) >= 2 else latest
    prev_5d = breadth_df.iloc[-6] if len(breadth_df) >= 6 else (breadth_df.iloc[0] if len(breadth_df) > 0 else latest)

    date_str = str(breadth_df.index[-1])[:10]
    pct_50 = float(latest["pct_above_50ma"])
    pct_200 = float(latest["pct_above_200ma"])

    pct_50_chg_1d = pct_50 - float(prev_1d["pct_above_50ma"])
    pct_50_chg_5d = pct_50 - float(prev_5d["pct_above_50ma"])

    pct_200_chg_1d = pct_200 - float(prev_1d["pct_above_200ma"])
    pct_200_chg_5d = pct_200 - float(prev_5d["pct_above_200ma"])

    sample_stocks = int(latest.get("total_stocks_50ma", 0))

    # 市場多空結構判定 (Minervini SEPA & Weinstein 階段體系)
    if pct_50 >= 70 and pct_200 >= 60:
        market_regime = "🔥 強勢全面多頭 (過熱警戒)"
        guidance = "全市場超過 70% 股票站上季線，多頭動能旺盛，但高檔追高風險增加，應嚴格汰弱留強。"
    elif pct_50 >= 50 and pct_200 >= 50:
        market_regime = "🌟 健康多頭擴張期"
        guidance = "站上季線與年線比例雙雙突破 50% 多空分水嶺，適合積極進場操作 VCP 突破型態。"
    elif pct_50 >= 50 > pct_200:
        market_regime = "👀 短多長空 (反彈築底期)"
        guidance = "短中期動能轉強但長線年線壓力仍在，宜鎖定領先突破創高的少數 Stage 2 強勢股。"
    elif pct_50 < 50 and pct_200 >= 50:
        market_regime = "⚠️ 長多短空 (高檔回檔整理)"
        guidance = "短線跌破季線個股增加，處於多頭回檔洗盤階段，留意是否有背離現象並控制持股水位。"
    elif pct_50 <= 25 and pct_200 <= 30:
        market_regime = "💎 恐慌超賣區 (醞釀波段底部)"
        guidance = "站上季線個股低於 25%，市場處於非理性恐慌，密切觀察抗跌拒跌且率先收縮的潛力黑馬。"
    else:
        market_regime = "⛔ 弱勢空頭 / 震盪走跌期"
        guidance = "低於 50% 多空分水嶺，多數個股處於空頭弱勢格局，操作宜保守嚴謹、保留現金防守。"

    caption = (
        f"📊 *全市場寬度指標報告 (Market Breadth)*\n"
        f"📅 日期: {date_str} (有效樣本: {sample_stocks:,} 檔)\n\n"
        f"• *高於 50MA (季線) 比例*: `{pct_50:.1f}%` (前日 `{pct_50_chg_1d:+.1f}%` | 5日 `{pct_50_chg_5d:+.1f}%`)\n"
        f"• *高於 200MA (年線) 比例*: `{pct_200:.1f}%` (前日 `{pct_200_chg_1d:+.1f}%` | 5日 `{pct_200_chg_5d:+.1f}%`)\n\n"
        f"🎯 *盤勢狀態*: {market_regime}\n"
        f"💡 *操作指引*: {guidance}"
    )

    return {
        "date": date_str,
        "pct_50": pct_50,
        "pct_200": pct_200,
        "pct_50_chg_1d": pct_50_chg_1d,
        "pct_50_chg_5d": pct_50_chg_5d,
        "pct_200_chg_1d": pct_200_chg_1d,
        "pct_200_chg_5d": pct_200_chg_5d,
        "sample_stocks": sample_stocks,
        "market_regime": market_regime,
        "guidance": guidance,
        "caption": caption,
    }
