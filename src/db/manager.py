"""SQLite 資料庫管理模組 (SQLite Database Manager Module).

此模組提供股票基本資料、歷史日 K 棒價格與 VCP/趨勢模板篩選結果的持久化存取。
"""

import json
import logging
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Optional, Union

import pandas as pd

from config.settings import get_settings

logger = logging.getLogger(__name__)


class DBManager:
    """SQLite 資料庫管理類別 (SQLite Database Manager).

    負責管理 stock_list, price_history, scan_results 三張資料表的操作。
    支援 Context Manager 協定，確保資源正確釋放。
    """

    def __init__(self, db_path: Optional[Union[Path, str]] = None) -> None:
        """初始化資料庫連線並建立所需資料表 (Initialize database connection and create tables).

        Args:
            db_path: 資料庫檔案路徑。若為 None，則預設讀取 Settings.DB_PATH。
        """
        if db_path is None:
            settings = get_settings()
            self.db_path = settings.DB_PATH
        elif isinstance(db_path, str) and db_path != ":memory:":
            self.db_path = Path(db_path)
        else:
            self.db_path = db_path

        # 若非記憶體資料庫，確保資料夾目錄存在
        if isinstance(self.db_path, Path):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_uri = str(self.db_path)
        else:
            self._db_uri = str(self.db_path)

        logger.info("正在連線至 SQLite 資料庫: %s", self._db_uri)
        self.conn: Optional[sqlite3.Connection] = sqlite3.connect(
            self._db_uri, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row

        # 開啟 WAL 模式提升並行讀寫效能
        try:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA foreign_keys=ON;")
        except sqlite3.Error as e:
            logger.warning("設定 SQLite PRAGMA 失敗: %s", e)

        self._create_tables()

    def _create_tables(self) -> None:
        """建立所需資料表與索引 (Create database tables and indices if not exist)."""
        if not self.conn:
            raise RuntimeError("資料庫連線尚未建立或已關閉")

        create_stock_list_sql = """
        CREATE TABLE IF NOT EXISTS stock_list (
            stock_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            market TEXT NOT NULL,
            yf_symbol TEXT NOT NULL,
            market_cap REAL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        """

        create_price_history_sql = """
        CREATE TABLE IF NOT EXISTS price_history (
            stock_id TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            adj_close REAL NOT NULL,
            volume INTEGER NOT NULL,
            PRIMARY KEY (stock_id, date)
        );
        """

        create_scan_results_sql = """
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date TEXT NOT NULL,
            stock_id TEXT NOT NULL,
            name TEXT NOT NULL,
            score REAL NOT NULL,
            trend_template_pass INTEGER NOT NULL,
            is_vcp INTEGER NOT NULL,
            details TEXT NOT NULL
        );
        """

        create_disposition_stocks_sql = """
        CREATE TABLE IF NOT EXISTS disposition_stocks (
            stock_id TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            market TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            disposition_type TEXT NOT NULL,
            matching_interval TEXT NOT NULL,
            reasons TEXT,
            condition_details TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_id, start_date)
        );
        """

        create_attention_stocks_sql = """
        CREATE TABLE IF NOT EXISTS attention_stocks (
            stock_id TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            market TEXT NOT NULL,
            notice_date TEXT NOT NULL,
            reasons TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_id, notice_date)
        );
        """

        create_indices_sql = [
            "CREATE INDEX IF NOT EXISTS idx_price_history_stock_date ON price_history(stock_id, date DESC);",
            "CREATE INDEX IF NOT EXISTS idx_scan_results_date ON scan_results(scan_date);",
            "CREATE INDEX IF NOT EXISTS idx_scan_results_stock_id ON scan_results(stock_id);",
            "CREATE INDEX IF NOT EXISTS idx_disposition_period ON disposition_stocks(start_date, end_date);",
            "CREATE INDEX IF NOT EXISTS idx_attention_date ON attention_stocks(notice_date DESC);",
        ]

        with self.conn:
            self.conn.execute(create_stock_list_sql)
            self.conn.execute(create_price_history_sql)
            self.conn.execute(create_scan_results_sql)
            self.conn.execute(create_disposition_stocks_sql)
            self.conn.execute(create_attention_stocks_sql)
            for idx_sql in create_indices_sql:
                self.conn.execute(idx_sql)

            # 自動遷移既有資料庫結構，新增 market_cap 欄位
            try:
                cursor = self.conn.execute("PRAGMA table_info(stock_list);")
                cols = [row[1] for row in cursor.fetchall()]
                if "market_cap" not in cols:
                    self.conn.execute("ALTER TABLE stock_list ADD COLUMN market_cap REAL DEFAULT 0;")
            except Exception as e:
                logger.debug("檢查/遷移 stock_list 欄位: %s", e)

        logger.debug("資料表與索引初始化完成")

    def upsert_stock_list(self, stocks: list[dict[str, Any]]) -> None:
        """批次新增或更新股票基本資料清單 (Batch insert or replace stock list).

        Args:
            stocks: 包含 stock_id, name, market, yf_symbol, (選填 updated_at, market_cap) 的字典清單
        """
        if not self.conn:
            raise RuntimeError("資料庫連線已關閉")
        if not stocks:
            logger.debug("傳入的股票清單為空，略過寫入")
            return

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        records = []
        for s in stocks:
            records.append(
                (
                    str(s["stock_id"]),
                    str(s["name"]),
                    str(s["market"]),
                    str(s["yf_symbol"]),
                    float(s.get("market_cap", 0.0)),
                    str(s.get("updated_at", now_str)),
                )
            )

        sql = """
        INSERT INTO stock_list (stock_id, name, market, yf_symbol, market_cap, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(stock_id) DO UPDATE SET
            name=excluded.name,
            market=excluded.market,
            yf_symbol=excluded.yf_symbol,
            market_cap=CASE WHEN excluded.market_cap > 0 THEN excluded.market_cap ELSE stock_list.market_cap END,
            updated_at=excluded.updated_at
        """

        with self.conn:
            self.conn.executemany(sql, records)

        logger.info("已成功寫入/更新 %d 檔股票基本資料", len(records))

    def get_market_caps(self) -> dict[str, float]:
        """取得資料庫中已快取的股票總市值字典."""
        if not self.conn:
            return {}
        try:
            cursor = self.conn.execute("SELECT stock_id, market_cap FROM stock_list WHERE market_cap > 0;")
            return {str(row[0]): float(row[1]) for row in cursor.fetchall()}
        except Exception:
            return {}

    def update_market_caps(self, market_cap_map: dict[str, float]) -> None:
        """批次更新股票總市值 (Batch update market caps)."""
        if not self.conn or not market_cap_map:
            return
        sql = "UPDATE stock_list SET market_cap = ? WHERE stock_id = ?;"
        records = [(float(mcap), str(s_id)) for s_id, mcap in market_cap_map.items()]
        with self.conn:
            self.conn.executemany(sql, records)

    def upsert_price_history(self, stock_id: str, df: pd.DataFrame) -> None:
        """新增或更新指定個股的歷史價格資料 (Insert or replace price history from DataFrame).

        Args:
            stock_id: 股票代號 (例如 '2330')
            df: 包含日 K 資料的 DataFrame，需包含 open, high, low, close, volume 及 date (或以 DatetimeIndex 為索引)
        """
        if not self.conn:
            raise RuntimeError("資料庫連線已關閉")
        if df is None or df.empty:
            logger.warning("個股 %s 傳入的價格資料為空，略過寫入", stock_id)
            return

        # 整理 DataFrame 欄位名稱並支援大小寫格式
        df_copy = df.copy()

        # 若 index 是 DatetimeIndex 或日期，重設為欄位
        if isinstance(df_copy.index, pd.DatetimeIndex) or (
            df_copy.index.name and df_copy.index.name.lower() in ("date", "datetime")
        ):
            df_copy = df_copy.reset_index()

        # 建立欄位名稱映射表
        col_map = {}
        for col in df_copy.columns:
            cleaned = str(col).strip().lower().replace(" ", "_")
            col_map[col] = cleaned
        df_copy = df_copy.rename(columns=col_map)

        # 尋找 date 欄位
        date_col = None
        for candidate in ("date", "datetime", "index"):
            if candidate in df_copy.columns:
                date_col = candidate
                break

        if not date_col:
            raise ValueError(f"個股 {stock_id} DataFrame 缺少日期欄位 (date)")

        # 轉換日期格式為 YYYY-MM-DD 字串
        if pd.api.types.is_datetime64_any_dtype(df_copy[date_col]):
            dates = df_copy[date_col].dt.strftime("%Y-%m-%d")
        else:
            dates = pd.to_datetime(df_copy[date_col]).dt.strftime("%Y-%m-%d")

        # 處理各價格與量欄位
        opens = pd.to_numeric(df_copy["open"], errors="coerce")
        highs = pd.to_numeric(df_copy["high"], errors="coerce")
        lows = pd.to_numeric(df_copy["low"], errors="coerce")
        closes = pd.to_numeric(df_copy["close"], errors="coerce")

        if "adj_close" in df_copy.columns:
            adj_closes = pd.to_numeric(df_copy["adj_close"], errors="coerce").fillna(closes)
        elif "adjclose" in df_copy.columns:
            adj_closes = pd.to_numeric(df_copy["adjclose"], errors="coerce").fillna(closes)
        else:
            adj_closes = closes

        volumes = pd.to_numeric(df_copy["volume"], errors="coerce").fillna(0).astype(int)

        # 過濾包含 NaN 價格的無效列
        valid_mask = ~(opens.isna() | highs.isna() | lows.isna() | closes.isna() | dates.isna())
        
        records = []
        for d, o, h, l, c, ac, v in zip(
            dates[valid_mask],
            opens[valid_mask],
            highs[valid_mask],
            lows[valid_mask],
            closes[valid_mask],
            adj_closes[valid_mask],
            volumes[valid_mask],
        ):
            records.append((str(stock_id), str(d), float(o), float(h), float(l), float(c), float(ac), int(v)))

        if not records:
            logger.warning("個股 %s 沒有有效的價格紀錄可寫入", stock_id)
            return

        sql = """
        INSERT OR REPLACE INTO price_history (stock_id, date, open, high, low, close, adj_close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """

        with self.conn:
            self.conn.executemany(sql, records)

        logger.debug("個股 %s 已寫入 %d 筆歷史價格資料", stock_id, len(records))

    def get_price_history(self, stock_id: str, days: int = 300) -> pd.DataFrame:
        """取得指定個股最近 N 天的歷史價格資料 (Get recent N days of price data for a stock).

        回傳資料依日期升冪排序 (由舊到新)，適合進行技術指標與型態計算。

        Args:
            stock_id: 股票代號 (例如 '2330')
            days: 查詢天數 (預設 300 天)

        Returns:
            pd.DataFrame: 包含 date, open, high, low, close, adj_close, volume 欄位的 DataFrame
        """
        if not self.conn:
            raise RuntimeError("資料庫連線已關閉")

        sql = """
        SELECT date, open, high, low, close, adj_close, volume
        FROM (
            SELECT date, open, high, low, close, adj_close, volume
            FROM price_history
            WHERE stock_id = ?
            ORDER BY date DESC
            LIMIT ?
        )
        ORDER BY date ASC;
        """

        df = pd.read_sql_query(
            sql,
            self.conn,
            params=(str(stock_id), int(days)),
        )

        if df.empty:
            return pd.DataFrame(
                columns=["date", "open", "high", "low", "close", "adj_close", "volume"]
            )

        # 確保型別正確
        df["date"] = df["date"].astype(str)
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["adj_close"] = df["adj_close"].astype(float)
        df["volume"] = df["volume"].astype(int)

        return df

    def get_all_stocks(self) -> list[dict[str, Any]]:
        """取得所有已儲存的股票清單 (Get all stocks from stock_list).

        Returns:
            list[dict]: 股票資訊字典清單，包含 stock_id, name, market, yf_symbol, updated_at
        """
        if not self.conn:
            raise RuntimeError("資料庫連線已關閉")

        sql = """
        SELECT stock_id, name, market, yf_symbol, market_cap, updated_at
        FROM stock_list
        ORDER BY stock_id ASC
        """

        cursor = self.conn.execute(sql)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def save_scan_results(self, results: list[dict[str, Any]]) -> None:
        """儲存篩選結果 (Save scan results to scan_results table).

        Args:
            results: 篩選結果字典清單，各元素包含:
                - scan_date (str): 掃描日期 (YYYY-MM-DD)
                - stock_id (str): 股票代號
                - name (str): 股票名稱
                - score (float): 綜合評分
                - trend_template_pass (int): 趨勢模板通過條件數或是否通過 (0 或 1 / 整數)
                - is_vcp (int): 是否符合 VCP 型態 (0 或 1)
                - details (dict | list | str): 詳細篩選指標 JSON 資料
        """
        if not self.conn:
            raise RuntimeError("資料庫連線已關閉")
        if not results:
            logger.debug("傳入的篩選結果為空，略過寫入")
            return

        records = []
        for r in results:
            details_val = r.get("details", {})
            if isinstance(details_val, (dict, list)):
                details_json = json.dumps(details_val, ensure_ascii=False)
            else:
                details_json = str(details_val)

            records.append(
                (
                    str(r["scan_date"]),
                    str(r["stock_id"]),
                    str(r["name"]),
                    float(r["score"]),
                    int(r["trend_template_pass"]),
                    int(r["is_vcp"]),
                    details_json,
                )
            )

        sql = """
        INSERT INTO scan_results (scan_date, stock_id, name, score, trend_template_pass, is_vcp, details)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """

        with self.conn:
            self.conn.executemany(sql, records)

        logger.info("已成功儲存 %d 筆篩選結果", len(records))

    def get_latest_price_date(self, stock_id: str) -> Optional[str]:
        """取得指定個股在 price_history 中的最新交易日期 (Get latest price date for a stock).

        Args:
            stock_id: 股票代號 (例如 '2330')

        Returns:
            Optional[str]: 最新日期字串 (YYYY-MM-DD)，若無資料則回傳 None
        """
        if not self.conn:
            raise RuntimeError("資料庫連線已關閉")

        sql = "SELECT MAX(date) AS latest_date FROM price_history WHERE stock_id = ?"
        cursor = self.conn.execute(sql, (str(stock_id),))
        row = cursor.fetchone()
        if row and row["latest_date"]:
            return str(row["latest_date"])
        return None

    def clear_today_cache(self, date_str: Optional[str] = None) -> int:
        """清除指定日期 (預設今日) 之價格快取，使下次掃描時強制重新自網路下載最新資料.

        Args:
            date_str: 日期字串 (YYYY-MM-DD)，若為 None 則預設為今日

        Returns:
            int: 刪除的筆數
        """
        if not self.conn:
            raise RuntimeError("資料庫連線已關閉")

        target_date = date_str or datetime.now().strftime("%Y-%m-%d")
        with self.conn:
            cursor = self.conn.execute("DELETE FROM price_history WHERE date = ?;", (target_date,))
            count = cursor.rowcount
            logger.info("已清除 %s 的歷史價格快取共 %d 筆", target_date, count)
            return count

    def clear_cache(
        self, clear_prices: bool = True, clear_stocks: bool = False, clear_results: bool = False
    ) -> None:
        """清除資料庫中的快取資料 (Clear cache from database).

        Args:
            clear_prices: 是否清除歷史價格資料表 (price_history)
            clear_stocks: 是否清除股票清單資料表 (stock_list)
            clear_results: 是否清除歷史篩選結果資料表 (scan_results)
        """
        if not self.conn:
            raise RuntimeError("資料庫連線已關閉")

        with self.conn:
            if clear_prices:
                self.conn.execute("DELETE FROM price_history;")
                logger.info("已清空 price_history 資料表")
            if clear_stocks:
                self.conn.execute("DELETE FROM stock_list;")
                logger.info("已清空 stock_list 資料表")
            if clear_results:
                self.conn.execute("DELETE FROM scan_results;")
                logger.info("已清空 scan_results 資料表")
            self.conn.execute("VACUUM;")

    def upsert_disposition_stocks(self, records: list[dict[str, Any]]) -> int:
        """批次新增或更新處置股票清單 (Batch insert or replace disposition stock records).

        Args:
            records: 包含 stock_id, stock_name, market, start_date, end_date,
                     disposition_type, matching_interval, reasons, condition_details 的字典清單

        Returns:
            int: 成功寫入或更新的筆數
        """
        if not self.conn:
            raise RuntimeError("資料庫連線已關閉")
        if not records:
            return 0

        sql = """
        INSERT OR REPLACE INTO disposition_stocks (
            stock_id, stock_name, market, start_date, end_date,
            disposition_type, matching_interval, reasons, condition_details, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
        """
        rows = [
            (
                str(r["stock_id"]).strip(),
                str(r.get("stock_name", "")).strip(),
                str(r.get("market", "")).strip(),
                str(r.get("start_date", "")).strip(),
                str(r.get("end_date", "")).strip(),
                str(r.get("disposition_type", "")).strip(),
                str(r.get("matching_interval", "5分撮合")).strip(),
                str(r.get("reasons", "")).strip(),
                str(r.get("condition_details", "")).strip(),
            )
            for r in records
            if r.get("stock_id") and r.get("start_date")
        ]

        with self.conn:
            self.conn.executemany(sql, rows)
            logger.debug("已更新 disposition_stocks 資料共 %d 筆", len(rows))
            return len(rows)

    def get_active_dispositions(
        self, query_date: Optional[str] = None
    ) -> dict[str, dict[str, Any]]:
        """取得特定日期（預設今日）處於處置期間中的股票字典.

        Args:
            query_date: 查詢日期字串 (YYYY-MM-DD)，若未指定則使用今日

        Returns:
            dict[str, dict[str, Any]]: key 為 stock_id，value 包含詳細處置資訊與剩餘營業日數
        """
        if not self.conn:
            raise RuntimeError("資料庫連線已關閉")

        target_date = query_date or datetime.now().strftime("%Y-%m-%d")
        sql = """
        SELECT stock_id, stock_name, market, start_date, end_date,
               disposition_type, matching_interval, reasons, condition_details
        FROM disposition_stocks
        WHERE start_date <= ? AND end_date >= ?;
        """
        cursor = self.conn.execute(sql, (target_date, target_date))
        rows = cursor.fetchall()
        result = {}

        for row in rows:
            sid = str(row["stock_id"])
            end_date = str(row["end_date"])
            rem_days = 0
            try:
                cur = datetime.strptime(target_date, "%Y-%m-%d").date()
                end_d = datetime.strptime(end_date, "%Y-%m-%d").date()
                if cur <= end_d:
                    d = cur
                    while d <= end_d:
                        if d.weekday() < 5:
                            rem_days += 1
                        d += timedelta(days=1)
            except Exception:
                rem_days = 0

            result[sid] = {
                "stock_id": sid,
                "stock_name": row["stock_name"],
                "market": row["market"],
                "start_date": row["start_date"],
                "end_date": end_date,
                "disposition_type": row["disposition_type"],
                "matching_interval": row["matching_interval"],
                "reasons": row["reasons"],
                "condition_details": row["condition_details"],
                "remaining_trading_days": rem_days,
                "is_exiting_soon": bool(0 < rem_days <= 2),
            }
        return result

    def upsert_attention_stocks(self, records: list[dict[str, Any]]) -> int:
        """批次新增或更新注意股票清單 (Batch insert or replace attention stock records)."""
        if not self.conn:
            raise RuntimeError("資料庫連線已關閉")
        if not records:
            return 0

        sql = """
        INSERT OR REPLACE INTO attention_stocks (
            stock_id, stock_name, market, notice_date, reasons, updated_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
        """
        rows = [
            (
                str(r["stock_id"]).strip(),
                str(r.get("stock_name", "")).strip(),
                str(r.get("market", "")).strip(),
                str(r.get("notice_date", "")).strip(),
                str(r.get("reasons", "")).strip(),
            )
            for r in records
            if r.get("stock_id") and r.get("notice_date")
        ]

        with self.conn:
            self.conn.executemany(sql, rows)
            logger.debug("已更新 attention_stocks 資料共 %d 筆", len(rows))
            return len(rows)

    def get_recent_attentions(
        self, days: int = 3, query_date: Optional[str] = None
    ) -> dict[str, dict[str, Any]]:
        """取得最近 N 日被列為注意股票的清單字典."""
        if not self.conn:
            raise RuntimeError("資料庫連線已關閉")

        target_date_obj = (
            datetime.strptime(query_date, "%Y-%m-%d").date()
            if query_date
            else date.today()
        )
        cutoff_date = (target_date_obj - timedelta(days=days)).strftime("%Y-%m-%d")

        sql = """
        SELECT stock_id, stock_name, market, notice_date, reasons
        FROM attention_stocks
        WHERE notice_date >= ?
        ORDER BY notice_date DESC;
        """
        cursor = self.conn.execute(sql, (cutoff_date,))
        rows = cursor.fetchall()
        result = {}
        for row in rows:
            sid = str(row["stock_id"])
            if sid not in result:
                result[sid] = {
                    "stock_id": sid,
                    "stock_name": row["stock_name"],
                    "market": row["market"],
                    "notice_date": row["notice_date"],
                    "reasons": row["reasons"],
                }
        return result

    def close(self) -> None:
        """關閉資料庫連線 (Close the database connection)."""
        if self.conn:
            try:
                self.conn.close()
                logger.debug("資料庫連線已關閉")
            except sqlite3.Error as e:
                logger.error("關閉資料庫連線時發生錯誤: %s", e)
            finally:
                self.conn = None

    def __enter__(self) -> "DBManager":
        """Context manager 進入點."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager 離開點，自動關閉連線."""
        self.close()
