"""歷史日 K 棒價格資料獲取模組 (Historical OHLCV Data Fetcher Module).

此模組使用 yfinance 下載台灣上市與上櫃股票歷史價格資料，
支援批次下載 (Batching)、本地 SQLite 快取檢查 (Caching)、成交量單位換算 (股轉張)、
以及多層級錯誤處理與重試機制。
"""

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf

from config.settings import get_settings
from src.db.manager import DBManager

logger = logging.getLogger(__name__)

# 設定 yfinance 時區快取至專案內目錄，避免 Windows AppData\Local\py-yfinance 衝突 (WinError 183)
try:
    _yf_cache_dir = Path(__file__).resolve().parent.parent / "data" / "yf_cache"
    _yf_cache_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(_yf_cache_dir))
except Exception as _e:
    logger.debug("設定 yfinance 快取目錄時略過: %s", _e)

# 標準輸出與儲存之價格欄位名稱清單
REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


class DataFetcher:
    """歷史股價資料獲取器 (Historical Stock Price Data Fetcher).

    負責自 yfinance 下載歷史日 K 棒資料，並將整理後的價格與成交張數存入 SQLite 資料庫。
    """

    def __init__(self, db_manager: DBManager) -> None:
        """初始化 DataFetcher.

        Args:
            db_manager: DBManager 資料庫管理實例，用於快取檢查與資料持久化
        """
        self.db_manager = db_manager
        self.settings = get_settings()

    def _clean_and_convert_df(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """清理 DataFrame、對齊欄位名稱並將成交量單位轉換為張數 (Clean df and convert volume to lots)."""
        if df is None or df.empty:
            return None

        clean_df = df.copy()

        # 若 columns 為 MultiIndex，取第 0 層
        if isinstance(clean_df.columns, pd.MultiIndex):
            clean_df.columns = clean_df.columns.get_level_values(0)

        # 欄位大小寫與空格標準化映射
        col_mapping = {}
        for col in clean_df.columns:
            col_str = str(col).strip()
            for std_col in REQUIRED_COLUMNS:
                if col_str.lower().replace("_", " ") == std_col.lower().replace("_", " "):
                    col_mapping[col] = std_col
                    break
        clean_df = clean_df.rename(columns=col_mapping)

        # 若缺少 Adj Close 則以 Close 填補
        if "Close" in clean_df.columns and "Adj Close" not in clean_df.columns:
            clean_df["Adj Close"] = clean_df["Close"]

        # 檢查是否具備所有必要欄位
        if not all(c in clean_df.columns for c in REQUIRED_COLUMNS):
            missing = [c for c in REQUIRED_COLUMNS if c not in clean_df.columns]
            logger.warning("DataFrame 缺少必要欄位: %s", missing)
            return None

        # 移除價格全為 NaN 的列
        clean_df = clean_df.dropna(subset=["Open", "High", "Low", "Close"])
        if clean_df.empty:
            return None

        # 確保價格欄位為浮點數
        for col in ["Open", "High", "Low", "Close", "Adj Close"]:
            clean_df[col] = pd.to_numeric(clean_df[col], errors="coerce")

        # 台灣股市 yfinance 成交量單位為「股」，需除以 1000 換算為「張」 (Lots)
        clean_df["Volume"] = (
            pd.to_numeric(clean_df["Volume"], errors="coerce")
            .fillna(0)
            .div(1000)
            .round()
            .astype(int)
        )

        return clean_df[REQUIRED_COLUMNS]

    def _extract_stock_df_from_batch(
        self,
        batch_df: pd.DataFrame,
        yf_symbol: str,
    ) -> Optional[pd.DataFrame]:
        """從批次下載的多檔股票 DataFrame 中擷取特定股票的 OHLCV (Extract single stock from batch df)."""
        if batch_df is None or batch_df.empty:
            return None

        try:
            stock_df: Optional[pd.DataFrame] = None

            if isinstance(batch_df.columns, pd.MultiIndex):
                if yf_symbol in batch_df.columns.levels[0]:
                    stock_df = batch_df[yf_symbol].copy()
                elif yf_symbol in batch_df.columns.levels[1]:
                    stock_df = batch_df.xs(yf_symbol, axis=1, level=1).copy()
                else:
                    return None
            else:
                stock_df = batch_df.copy()

            return self._clean_and_convert_df(stock_df)
        except Exception as e:
            logger.debug("解析個股 %s 批次資料時發生例外: %s", yf_symbol, e)
            return None

    def fetch_single(self, yf_symbol: str, period: Optional[str] = None) -> pd.DataFrame:
        """下載單一股票的歷史 OHLCV 價格資料 (Download OHLCV data for a single stock).

        Args:
            yf_symbol: yfinance 股票代號 (例如 '2330.TW', '6488.TWO')
            period: 需求期間 (預設使用 settings.DATA_PERIOD, 例如 '5y')

        Returns:
            pd.DataFrame: 包含 Open, High, Low, Close, Adj Close, Volume 欄位的價格資料
        """
        data_period = period or self.settings.DATA_PERIOD
        logger.info("正在下載個股價格資料: %s (期間: %s)", yf_symbol, data_period)
        try:
            raw_df = yf.download(
                yf_symbol,
                period=data_period,
                progress=False,
                auto_adjust=False,
            )

            cleaned_df = self._clean_and_convert_df(raw_df)
            if cleaned_df is None or cleaned_df.empty:
                logger.warning("個股 %s 下載後無有效數據", yf_symbol)
                return pd.DataFrame(columns=REQUIRED_COLUMNS)

            return cleaned_df
        except Exception as e:
            logger.warning("下載個股 %s 價格資料失敗: %s", yf_symbol, e)
            return pd.DataFrame(columns=REQUIRED_COLUMNS)

    def fetch_benchmark(self, symbol: str = "^TWII", period: Optional[str] = None) -> pd.DataFrame:
        """下載大盤基準指數歷史資料並存入資料庫 (Download benchmark index e.g. TAIEX ^TWII).

        Args:
            symbol: 大盤代號 (預設 '^TWII' 加權指數)
            period: 需求期間 (預設使用 settings.DATA_PERIOD)

        Returns:
            pd.DataFrame: 大盤指數日 K 資料
        """
        data_period = period or self.settings.DATA_PERIOD
        logger.info("正在下載大盤基準資料: %s (期間: %s)", symbol, data_period)
        try:
            raw_df = yf.download(
                symbol,
                period=data_period,
                progress=False,
                auto_adjust=False,
            )
            cleaned_df = self._clean_and_convert_df(raw_df)
            if cleaned_df is not None and not cleaned_df.empty:
                self.db_manager.upsert_price_history(symbol, cleaned_df)
                logger.info("大盤指數 %s 已成功寫入資料庫 (%d 筆)", symbol, len(cleaned_df))
                return cleaned_df
        except Exception as e:
            logger.warning("下載大盤指數 %s 失敗: %s", symbol, e)

        return pd.DataFrame(columns=REQUIRED_COLUMNS)

    def fetch_all(
        self,
        stocks: List[Dict[str, Any]],
        days: int = 1300,
        force_fetch: bool = False,
        period: Optional[str] = None,
    ) -> Dict[str, pd.DataFrame]:
        """批次下載所有指定股票之歷史價格資料並存入資料庫 (Download all stocks in batches).

        Args:
            stocks: 包含 stock_id, name, market, yf_symbol 的股票清單字典
            days: 查詢天數 (預設 1300 天，涵蓋 5 年)
            force_fetch: 是否強制重新自網路下載
            period: 下載週期 (預設使用 settings.DATA_PERIOD，例如 '5y')

        Returns:
            Dict[str, pd.DataFrame]: 股票代號 (stock_id) 對應價格 DataFrame 的字典
        """
        data_period = period or self.settings.DATA_PERIOD
        if not stocks:
            logger.warning("傳入的股票清單為空")
            return {}

        today_str = datetime.now().strftime("%Y-%m-%d")
        results: Dict[str, pd.DataFrame] = {}
        stocks_to_fetch: List[Dict[str, Any]] = []

        # 1. 檢查快取：若非強制重新下載且今日已有最新資料則略過
        for stock in stocks:
            stock_id = str(stock["stock_id"])

            if not force_fetch:
                latest_date = self.db_manager.get_latest_price_date(stock_id)
                if latest_date == today_str:
                    logger.debug("個股 %s 今日資料已快取，自資料庫載入", stock_id)
                    cached_df = self.db_manager.get_price_history(stock_id, days=days)
                    if not cached_df.empty:
                        results[stock_id] = cached_df
                        continue

            stocks_to_fetch.append(stock)

        if not stocks_to_fetch:
            logger.info("所有 %d 檔股票今日資料皆已存在於資料庫，無需重複下載", len(stocks))
            return results

        logger.info(
            "總計 %d 檔股票，其中 %d 檔已快取，開始下載其餘 %d 檔股票資料 (期間: %s)...",
            len(stocks),
            len(results),
            len(stocks_to_fetch),
            data_period,
        )

        # 2. 分批下載 (每批 50 檔)
        batch_size = 50
        total_batches = (len(stocks_to_fetch) + batch_size - 1) // batch_size

        for batch_idx in range(total_batches):
            batch = stocks_to_fetch[batch_idx * batch_size : (batch_idx + 1) * batch_size]
            batch_num = batch_idx + 1
            logger.info("Downloading batch %d/%d (%d stocks, period=%s)...", batch_num, total_batches, len(batch), data_period)

            symbol_to_id = {str(s["yf_symbol"]): str(s["stock_id"]) for s in batch}
            symbols_list = list(symbol_to_id.keys())

            try:
                # 批次呼叫 yfinance.download
                batch_df = yf.download(
                    tickers=symbols_list,
                    period=data_period,
                    group_by="ticker",
                    threads=True,
                    progress=False,
                    auto_adjust=False,
                )

                # 逐檔提取資料並寫入資料庫
                for yf_sym, s_id in symbol_to_id.items():
                    try:
                        stock_df = self._extract_stock_df_from_batch(batch_df, yf_sym)
                        if stock_df is not None and not stock_df.empty:
                            self.db_manager.upsert_price_history(s_id, stock_df)
                            results[s_id] = stock_df
                        else:
                            # 若批次提取失敗，嘗試單檔下載作為備援
                            logger.debug("批次資料中無 %s (%s)，嘗試單檔下載備援", s_id, yf_sym)
                            single_df = self.fetch_single(yf_sym, period=data_period)
                            if not single_df.empty:
                                self.db_manager.upsert_price_history(s_id, single_df)
                                results[s_id] = single_df
                            else:
                                logger.warning("個股 %s (%s) 無法取得價格資料", s_id, yf_sym)
                    except Exception as single_err:
                        logger.warning("處理個股 %s (%s) 價格時失敗: %s", s_id, yf_sym, single_err)
                        continue

            except Exception as batch_err:
                logger.warning(
                    "批次 %d/%d 下載發生錯誤: %s，嘗試逐檔單獨下載備援...",
                    batch_num,
                    total_batches,
                    batch_err,
                )
                for yf_sym, s_id in symbol_to_id.items():
                    try:
                        single_df = self.fetch_single(yf_sym, period=data_period)
                        if not single_df.empty:
                            self.db_manager.upsert_price_history(s_id, single_df)
                            results[s_id] = single_df
                    except Exception as fallback_err:
                        logger.warning("備援下載個股 %s 失敗: %s", s_id, fallback_err)

            # 每批次間隔 2 秒 (最後一批結束後不需 sleep)
            if batch_idx < total_batches - 1:
                time.sleep(2)

        logger.info("所有股票下載作業完成，成功取得 %d 檔股票價格資料", len(results))
        return results
