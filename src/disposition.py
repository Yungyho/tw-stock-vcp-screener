"""處置股票與注意股票管理模組 (Disposition & Attention Stock Module).

此模組負責從台灣證券交易所 (TWSE) 與 證券櫃檯買賣中心 (TPEx) 抓取最新處置與注意有價證券名單，
解析處置起訖日期、撮合分盤週期（如 5 分鐘 / 20 分鐘撮合）、預收款券等限制條件，
並計算剩餘處置交易日，提供給選股器 (Screener) 進行動態均量保護以及給報告模組標註警示。
"""

import logging
import re
import ssl
import urllib.request
import json
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
from pathlib import Path
import pandas as pd

from config.settings import get_settings
from src.db.manager import DBManager

logger = logging.getLogger(__name__)


def roc_to_ad(s: str) -> str:
    """將民國年日期轉換為西元 YYYY-MM-DD 格式.

    支援格式範例:
        - '115/09/18' -> '2026-09-18'
        - '1150918'   -> '2026-09-18'
        - '115.09.18' -> '2026-09-18'
        - '2026-09-18' -> '2026-09-18'
    """
    if not s:
        return ""
    s = s.strip()
    # 已經是西元 YYYY-MM-DD 或 YYYY/MM/DD
    m_ad = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", s)
    if m_ad:
        y, m, d = m_ad.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"

    # 民國格式: 115/09/18, 115.09.18, 115-09-18
    m_roc_sep = re.match(r"^(\d{2,3})[./-](\d{1,2})[./-](\d{1,2})$", s)
    if m_roc_sep:
        y, m, d = m_roc_sep.groups()
        return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"

    # 民國無分隔符號: 1150918 (7碼) 或 990918 (6碼)
    m_roc_compact = re.match(r"^(\d{2,3})(\d{2})(\d{2})$", s)
    if m_roc_compact:
        y, m, d = m_roc_compact.groups()
        return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"

    return s


def calculate_remaining_trading_days(
    end_date_str: str,
    query_date_str: Optional[str] = None
) -> int:
    """計算自 query_date (含) 起至 end_date (含) 之剩餘營業日數 (扣除週六、週日).

    Args:
        end_date_str: 處置結束日 (YYYY-MM-DD)
        query_date_str: 查詢日期 (YYYY-MM-DD)，若未提供則預設為今日

    Returns:
        int: 剩餘交易日數。若已過期則回傳 0。
    """
    if not end_date_str:
        return 0
    try:
        end_d = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        if query_date_str:
            start_d = datetime.strptime(query_date_str, "%Y-%m-%d").date()
        else:
            start_d = date.today()

        if start_d > end_d:
            return 0

        # 計算營業日 (Monday to Friday)
        cur = start_d
        count = 0
        while cur <= end_d:
            if cur.weekday() < 5:  # 0~4 為週一至週五
                count += 1
            cur += timedelta(days=1)
        return count
    except Exception as e:
        logger.warning("計算剩餘處置天數失敗 (%s, %s): %s", end_date_str, query_date_str, e)
        return 0


class DispositionManager:
    """處置與注意股票管理核心類別."""

    def __init__(self, db_manager: Optional[DBManager] = None):
        self.settings = get_settings()
        self._db = db_manager
        self._ssl_ctx = ssl._create_unverified_context()

    @property
    def db(self) -> DBManager:
        if self._db is None:
            self._db = DBManager(self.settings.DB_PATH)
        return self._db

    def fetch_twse_dispositions(self) -> List[Dict[str, Any]]:
        """抓取台灣證券交易所 (TWSE 上市) 處置有價證券名單."""
        url = "https://www.twse.com.tw/rwd/zh/announcement/punish?response=json"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        records = []
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            
            raw_data = data.get("data", [])
            for row in raw_data:
                # 欄位順序: 編號, 公布日期, 證券代號, 證券名稱, 累計, 處置條件, 處置起迄時間, 處置措施, 處置內容, 備註
                if len(row) < 8:
                    continue
                code = str(row[2]).strip()
                name = str(row[3]).strip()
                period_str = str(row[6]).strip()
                measures = str(row[7]).strip()
                details = str(row[8]).strip() if len(row) > 8 else ""

                # 解析起訖日 (例如 "115/09/18～115/09/30")
                period_parts = re.split(r"[～~至\-]", period_str)
                start_date = roc_to_ad(period_parts[0]) if len(period_parts) > 0 else ""
                end_date = roc_to_ad(period_parts[1]) if len(period_parts) > 1 else ""

                # 撮合分盤解析
                matching_interval = "5分撮合"
                if "二十" in details or "20" in details or "第二" in measures:
                    matching_interval = "20分撮合"
                elif "五" in details or "5" in details or "第一" in measures:
                    matching_interval = "5分撮合"
                else:
                    m = re.search(r"每([0-9０-９一二三四五六七八九十]+)分鐘", details)
                    if m:
                        matching_interval = f"{m.group(1)}分撮合"

                records.append({
                    "stock_id": code,
                    "stock_name": name,
                    "market": "listed",
                    "start_date": start_date,
                    "end_date": end_date,
                    "disposition_type": measures,
                    "matching_interval": matching_interval,
                    "reasons": str(row[5]).strip() if len(row) > 5 else "",
                    "condition_details": details,
                })
            logger.info("成功抓取 TWSE 上市處置股票共 %d 筆", len(records))
        except Exception as e:
            logger.error("抓取 TWSE 上市處置股票失敗: %s", e)
        return records

    def fetch_tpex_dispositions(self) -> List[Dict[str, Any]]:
        """抓取證券櫃檯買賣中心 (TPEx 上櫃) 處置有價證券名單."""
        url = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        records = []
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            for item in data:
                code = str(item.get("SecuritiesCompanyCode", "")).strip()
                name = str(item.get("CompanyName", "")).strip()
                period_str = str(item.get("DispositionPeriod", "")).strip()
                cond = str(item.get("DisposalCondition", "")).strip()
                reasons = str(item.get("DispositionReasons", "")).strip()

                period_parts = re.split(r"[～~至\-]", period_str)
                start_date = roc_to_ad(period_parts[0]) if len(period_parts) > 0 else ""
                end_date = roc_to_ad(period_parts[1]) if len(period_parts) > 1 else ""

                # 撮合分盤解析
                matching_interval = "5分撮合"
                if "20分鐘" in cond or "二十分鐘" in cond:
                    matching_interval = "20分撮合"
                elif "5分鐘" in cond or "五分鐘" in cond:
                    matching_interval = "5分撮合"
                else:
                    m = re.search(r"每([0-9０-９一二三四五六七八九十]+)分鐘", cond)
                    if m:
                        matching_interval = f"{m.group(1)}分撮合"

                # 處置等級
                disposition_type = "處置股票"
                if "第二次" in cond or "20分" in matching_interval:
                    disposition_type = "第二次處置"
                elif "第一次" in cond or "5分" in matching_interval:
                    disposition_type = "第一次處置"

                records.append({
                    "stock_id": code,
                    "stock_name": name,
                    "market": "otc",
                    "start_date": start_date,
                    "end_date": end_date,
                    "disposition_type": disposition_type,
                    "matching_interval": matching_interval,
                    "reasons": reasons,
                    "condition_details": cond,
                })
            logger.info("成功抓取 TPEx 上櫃處置股票共 %d 筆", len(records))
        except Exception as e:
            logger.error("抓取 TPEx 上櫃處置股票失敗: %s", e)
        return records

    def fetch_all_dispositions(self) -> List[Dict[str, Any]]:
        """合併抓取上市與上櫃全部處置股票名單."""
        twse_list = self.fetch_twse_dispositions()
        tpex_list = self.fetch_tpex_dispositions()
        return twse_list + tpex_list

    def fetch_twse_attentions(self, days: int = 5) -> List[Dict[str, Any]]:
        """抓取 TWSE 最近幾日的注意股票公告."""
        end_d = date.today()
        start_d = end_d - timedelta(days=days)
        start_str = start_d.strftime("%Y%m%d")
        end_str = end_d.strftime("%Y%m%d")
        url = f"https://www.twse.com.tw/rwd/zh/announcement/notice?response=json&startDate={start_str}&endDate={end_str}"
        headers = {"User-Agent": "Mozilla/5.0"}
        records = []
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for r in data.get("data", []):
                if len(r) < 6:
                    continue
                code = str(r[1]).strip()
                name = str(r[2]).strip()
                reasons = str(r[4]).strip()
                notice_date = roc_to_ad(str(r[5]).strip())
                records.append({
                    "stock_id": code,
                    "stock_name": name,
                    "market": "listed",
                    "notice_date": notice_date,
                    "reasons": reasons,
                })
        except Exception as e:
            logger.debug("抓取 TWSE 注意股票失敗: %s", e)
        return records

    def fetch_tpex_attentions(self) -> List[Dict[str, Any]]:
        """抓取 TPEx 上櫃注意股票資訊."""
        url = "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information"
        headers = {"User-Agent": "Mozilla/5.0"}
        records = []
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for item in data:
                code = str(item.get("SecuritiesCompanyCode", "")).strip()
                name = str(item.get("CompanyName", "")).strip()
                reasons = str(item.get("TradingInformation", "")).strip()
                notice_date = roc_to_ad(str(item.get("Date", "")).strip())
                records.append({
                    "stock_id": code,
                    "stock_name": name,
                    "market": "otc",
                    "notice_date": notice_date,
                    "reasons": reasons,
                })
        except Exception as e:
            logger.debug("抓取 TPEx 注意股票失敗: %s", e)
        return records

    def sync_to_database(self) -> Dict[str, int]:
        """抓取並同步處置與注意股票至本地 SQLite 資料庫."""
        dispositions = self.fetch_all_dispositions()
        attentions = self.fetch_twse_attentions() + self.fetch_tpex_attentions()

        disp_count = self.db.upsert_disposition_stocks(dispositions)
        attn_count = self.db.upsert_attention_stocks(attentions)

        logger.info("處置與注意資料同步完成: 處置股 %d 筆, 注意股 %d 筆", disp_count, attn_count)
        return {"disposition_count": disp_count, "attention_count": attn_count}

    def get_active_dispositions(self, query_date: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """取得當前 (或指定日期) 處於處置期間中的所有股票字典.

        Key 為 stock_id，Value 為處置資訊 (包含 remaining_trading_days, is_exiting_soon 等).
        """
        return self.db.get_active_dispositions(query_date)

    def get_stock_disposition_info(
        self, stock_id: str, query_date: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """取得特定個股的當前處置資訊 (若無處置則回傳 None)."""
        active = self.get_active_dispositions(query_date)
        return active.get(str(stock_id))

    def get_recent_attention_stocks(self, days: int = 3) -> Dict[str, Dict[str, Any]]:
        """取得最近幾日內被列為注意股票的清單字典."""
        return self.db.get_recent_attentions(days=days)
