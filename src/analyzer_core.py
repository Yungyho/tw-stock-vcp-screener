"""個股分析核心邏輯模組 (Stock Analyzer Core Module).

將個股的四階段判定、Trend Template 9 項檢測、VCP 收斂型態偵測、
Beta/市值/成交金額等指標整合為一個可重用的核心函數，
回傳結構化結果字典，供 Console (analyze.py) 與 Telegram Bot 兩端呼叫。
"""

import logging
import re
from typing import Any, Dict, List, Optional

import pandas as pd

from config.settings import get_settings, Settings
from src.beta import calculate_beta
from src.data_fetcher import DataFetcher
from src.db.manager import DBManager
from src.market_cap import fetch_single_market_cap
from src.scorer import calculate_score
from src.stage_analyzer import analyze_market_stage
from src.trend_template import check_trend_template
from src.vcp_detector import detect_vcp

logger = logging.getLogger(__name__)


def resolve_stock_symbol(db: DBManager, stock_input: str) -> Dict[str, Any]:
    """從資料庫或規則解析股票代號、名稱與 yfinance 代號."""
    s_id = str(stock_input).strip().upper()
    s_id = re.sub(r"\.(TW|TWO)$", "", s_id, flags=re.IGNORECASE)

    # 1. 查詢本地資料庫 stock_list
    all_stocks = db.get_all_stocks()
    for s in all_stocks:
        if str(s["stock_id"]) == s_id or str(s["name"]) == s_id:
            return {
                "stock_id": str(s["stock_id"]),
                "name": str(s["name"]),
                "market": str(s.get("market", "listed")),
                "yf_symbol": str(s.get("yf_symbol", f"{s['stock_id']}.TW")),
                "market_cap": float(s.get("market_cap", 0.0)),
            }

    # 2. 若資料庫無此代號，預設給予推論 (預設上市 .TW)
    return {
        "stock_id": s_id,
        "name": s_id,
        "market": "listed",
        "yf_symbol": f"{s_id}.TW",
        "market_cap": 0.0,
    }


def prepare_benchmark(
    db: DBManager,
    fetcher: DataFetcher,
    symbol: str = "^TWII",
    force_fetch: bool = False,
) -> pd.DataFrame:
    """載入並準備大盤基準數據.

    Args:
        db: 資料庫管理器
        fetcher: 資料抓取器
        symbol: 大盤基準代號 (預設 '^TWII' TAIEX, 上櫃可傳 '006201.TWO' TPEx)
        force_fetch: 是否強制重新下載

    Returns:
        pd.DataFrame: 已轉換大寫欄位的大盤 DataFrame
    """
    benchmark_df = db.get_price_history(symbol, days=520)
    if benchmark_df.empty or force_fetch:
        fetcher.fetch_benchmark(symbol, period="18mo")
        benchmark_df = db.get_price_history(symbol, days=520)

    if not benchmark_df.empty:
        benchmark_df = benchmark_df.rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "adj_close": "Adj Close",
                "volume": "Volume",
            }
        )

    return benchmark_df


def analyze_stock(
    db: DBManager,
    fetcher: DataFetcher,
    stock_input: str,
    benchmark_df: pd.DataFrame,
    force_fetch: bool = False,
    settings: Optional[Settings] = None,
) -> Optional[Dict[str, Any]]:
    """分析單檔個股並回傳結構化診斷結果 (不做任何 I/O 輸出).

    Args:
        db: 資料庫管理器
        fetcher: 資料抓取器
        stock_input: 股票代號或名稱
        benchmark_df: 已準備好的大盤基準 DataFrame
        force_fetch: 是否強制重新下載最新數據
        settings: 系統設定，若為 None 則自動讀取

    Returns:
        Optional[Dict[str, Any]]: 結構化診斷結果字典，若失敗回傳 None
    """
    if settings is None:
        settings = get_settings()

    stock_info = resolve_stock_symbol(db, stock_input)
    stock_market = stock_info.get("market", "listed")
    criteria = settings.get_market_criteria(stock_market)
    stock_id = stock_info["stock_id"]
    yf_symbol = stock_info["yf_symbol"]

    # 1. 載入或下載歷史股價
    df = pd.DataFrame()
    if not force_fetch:
        df = db.get_price_history(stock_id, days=520)

    if df.empty or len(df) < 200 or force_fetch:
        logger.info("正在下載 %s (%s) 最新歷史數據...", stock_id, yf_symbol)
        # 嘗試下載 .TW
        df_downloaded = fetcher.fetch_single(yf_symbol, period="18mo")
        if df_downloaded.empty:
            # 嘗試上櫃 .TWO
            alt_sym = f"{stock_id}.TWO"
            df_downloaded = fetcher.fetch_single(alt_sym, period="18mo")
            if not df_downloaded.empty:
                stock_info["yf_symbol"] = alt_sym
                stock_info["market"] = "otc"

        if not df_downloaded.empty:
            db.upsert_price_history(stock_id, df_downloaded)
            df = db.get_price_history(stock_id, days=520)

    if df.empty or len(df) < 252:
        logger.warning("股票代號 %s 數據不足 252 個交易日", stock_input)
        return None

    # 2. 獲取 / 更新總市值
    market_cap = float(stock_info.get("market_cap", 0.0))
    if market_cap <= 0 or force_fetch:
        try:
            fetched_cap = fetch_single_market_cap(stock_info["yf_symbol"])
            if fetched_cap and fetched_cap > 0:
                market_cap = fetched_cap
                db.update_market_caps({stock_id: market_cap})
        except Exception as e:
            logger.warning("取得 %s 市值失敗: %s", stock_id, e)

    # 3. 轉換大寫欄位以供各模組運算
    col_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "adj_close": "Adj Close",
        "volume": "Volume",
    }
    analysis_df = df.rename(columns=col_map)

    # 4. 計算 1 年期 Beta (依市場對應大盤基準: 上市 vs TAIEX, 上櫃 vs TPEx)
    beta_1y = None
    # 若傳入的 benchmark_df 不對應該股市場，嘗試載入正確的大盤
    correct_benchmark_symbol = criteria["benchmark_symbol"]
    if not benchmark_df.empty:
        beta_1y = calculate_beta(analysis_df, benchmark_df, lookback_days=252)

    # 5. 計算 4 大市場階段 (Stage 1~4)
    stage_res = analyze_market_stage(analysis_df)

    # 6. 計算 Trend Template 9 條件
    tt_result = check_trend_template(analysis_df)

    # 6b. 取得處置與注意股票資訊 (Disposition & Attention Info)
    from src.disposition import DispositionManager
    disp_mgr = DispositionManager(db)
    disposition_info = disp_mgr.get_stock_disposition_info(stock_id)
    recent_attns = disp_mgr.get_recent_attention_stocks(days=5)
    attention_info = recent_attns.get(stock_id)

    # 7. 計算 VCP 波動收斂型態
    vcp_result = detect_vcp(
        analysis_df,
        strict_mode=settings.VCP_STRICT_MODE,
        strict_convergence=settings.VCP_STRICT_CONVERGENCE,
        max_tightness=settings.VCP_MAX_TIGHTNESS,
        max_pivot_distance=settings.VCP_MAX_PIVOT_DISTANCE,
        max_base_depth=settings.VCP_MAX_BASE_DEPTH,
        scan_mode=getattr(settings, "VCP_SCAN_MODE", "standard"),
        include_breakout=getattr(settings, "INCLUDE_RECENT_BREAKOUT", True),
        include_retest=getattr(settings, "INCLUDE_PIVOT_RETEST", True),
        disposition_info=disposition_info,
    )

    # 8. 計算綜合評分
    score = calculate_score(
        trend_result=tt_result,
        vcp_result=vcp_result,
        df=analysis_df,
        market_df=benchmark_df if not benchmark_df.empty else None,
        disposition_info=disposition_info,
    )

    # 9. 計算 20 日均成交金額
    recent_20 = df.tail(20)
    avg_vol = float(recent_20["volume"].mean())
    last_close = float(df.iloc[-1]["close"])
    turnover_twd = avg_vol * 1000.0 * last_close

    return {
        "stock_info": stock_info,
        "df": df,
        "analysis_df": analysis_df,
        "stage_res": stage_res,
        "tt_result": tt_result,
        "vcp_result": vcp_result,
        "score": score,
        "beta_1y": beta_1y,
        "market_cap": market_cap,
        "turnover_twd": turnover_twd,
        "benchmark_name": criteria["benchmark_name"],
        "benchmark_symbol": correct_benchmark_symbol,
        "disposition_info": disposition_info,
        "attention_info": attention_info,
    }
