"""個股市值獲取模組 (Market Capitalization Fetcher Module).

負責取得上市與上櫃股票之總市值 (Market Cap in TWD)，並快取至 SQLite 資料庫。
支援 Yahoo Finance fast_info 多線程批次獲取與政府公開數據備援。
"""

import concurrent.futures
import logging
from typing import Any, Dict, List, Optional

import requests
import urllib3
import yfinance as yf

from src.db.manager import DBManager

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)


def fetch_single_market_cap(yf_symbol: str) -> Optional[float]:
    """獲取單一股票的總市值 (TWD)."""
    try:
        ticker = yf.Ticker(yf_symbol)
        fast_info = getattr(ticker, "fast_info", None)
        if fast_info and hasattr(fast_info, "market_cap"):
            mcap = fast_info.market_cap
            if mcap and mcap > 0:
                return float(mcap)

        # 備援：info 字典
        info = ticker.info
        mcap = info.get("marketCap")
        if mcap and mcap > 0:
            return float(mcap)
    except Exception as e:
        logger.debug("獲取 %s 市值失敗: %s", yf_symbol, e)
    return None


def fetch_and_update_market_caps(
    db: DBManager,
    stocks: List[Dict[str, Any]],
    force_update: bool = False,
    max_workers: int = 10,
) -> Dict[str, float]:
    """多線程批次抓取並更新所有股票的最新總市值.

    Args:
        db: DBManager 實例
        stocks: 股票字典清單
        force_update: 是否強制重新線上抓取
        max_workers: 平行線程數

    Returns:
        Dict[str, float]: stock_id 對應總市值 (TWD) 的字典
    """
    logger.info("開始更新股票總市值資料 (共 %d 檔)...", len(stocks))
    market_caps: Dict[str, float] = {}
    stocks_to_fetch = []

    # 1. 先讀取資料庫中既有的市值快取
    existing_caps = db.get_market_caps()
    for s in stocks:
        s_id = str(s["stock_id"])
        if not force_update and s_id in existing_caps and existing_caps[s_id] > 0:
            market_caps[s_id] = existing_caps[s_id]
        else:
            stocks_to_fetch.append(s)

    if not stocks_to_fetch:
        logger.info("所有股票市值均已自本地資料庫載入 (共 %d 檔)", len(market_caps))
        return market_caps

    logger.info("需自網路下載 %d 檔股票最新市值...", len(stocks_to_fetch))

    def _worker(stock: Dict[str, Any]) -> tuple[str, Optional[float]]:
        s_id = str(stock["stock_id"])
        yf_sym = str(stock.get("yf_symbol", f"{s_id}.TW"))
        mcap = fetch_single_market_cap(yf_sym)
        return s_id, mcap

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_worker, s) for s in stocks_to_fetch]
        for f in concurrent.futures.as_completed(futures):
            s_id, mcap = f.result()
            if mcap and mcap > 0:
                market_caps[s_id] = mcap

    # 2. 將新抓取的市值批次寫入資料庫快取
    if market_caps:
        db.update_market_caps(market_caps)
        logger.info("股票總市值更新完成，已成功儲存 %d 檔市值資料", len(market_caps))

    return market_caps
