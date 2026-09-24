"""專案基礎架構與資料庫管理單元測試 (Unit tests for scaffolding and DBManager)."""

import json
from pathlib import Path
import pandas as pd
import pytest

from config.settings import Settings, get_settings
from src.db.manager import DBManager


def test_settings_default_values():
    """測試 Settings 預設值與型別轉換."""
    settings = get_settings()
    assert isinstance(settings.MIN_VOLUME, int)
    assert settings.MIN_VOLUME == 5000
    assert isinstance(settings.MIN_PRICE, float)
    assert settings.MIN_PRICE == 50.0
    assert settings.EXCLUDE_ETF is True
    assert settings.EXCLUDE_KY is True
    assert settings.EXCLUDE_TDR is True
    assert isinstance(settings.TREND_TEMPLATE_MIN_PASS, int)
    assert settings.SCAN_HOUR == 17
    assert settings.SCAN_MINUTE == 0
    assert settings.TIMEZONE == "Asia/Taipei"
    assert isinstance(settings.DB_PATH, Path)
    assert settings.LOG_LEVEL == "INFO"


def test_db_manager_crud():
    """測試 DBManager 基本操作與 Context Manager."""
    with DBManager(":memory:") as db:
        # 1. 測試 upsert_stock_list & get_all_stocks
        test_stocks = [
            {
                "stock_id": "2330",
                "name": "台積電",
                "market": "listed",
                "yf_symbol": "2330.TW",
                "updated_at": "2026-08-19 12:00:00",
            },
            {
                "stock_id": "2454",
                "name": "聯發科",
                "market": "listed",
                "yf_symbol": "2454.TW",
            },
        ]
        db.upsert_stock_list(test_stocks)
        all_stocks = db.get_all_stocks()
        assert len(all_stocks) == 2
        assert all_stocks[0]["stock_id"] == "2330"
        assert all_stocks[0]["name"] == "台積電"
        assert all_stocks[1]["stock_id"] == "2454"

        # 2. 測試 upsert_price_history & get_price_history & get_latest_price_date
        price_data = pd.DataFrame(
            {
                "date": ["2026-08-15", "2026-08-18", "2026-08-19"],
                "open": [950.0, 955.0, 960.0],
                "high": [960.0, 965.0, 970.0],
                "low": [945.0, 950.0, 955.0],
                "close": [955.0, 960.0, 968.0],
                "adj_close": [955.0, 960.0, 968.0],
                "volume": [30000, 28000, 35000],
            }
        )
        db.upsert_price_history("2330", price_data)

        latest_date = db.get_latest_price_date("2330")
        assert latest_date == "2026-08-19"

        df_hist = db.get_price_history("2330", days=2)
        assert len(df_hist) == 2
        # 確保依日期升冪排序
        assert list(df_hist["date"]) == ["2026-08-18", "2026-08-19"]
        assert df_hist.iloc[-1]["close"] == 968.0

        # 測試未知個股日期
        assert db.get_latest_price_date("9999") is None

        # 3. 測試 save_scan_results
        scan_results = [
            {
                "scan_date": "2026-08-19",
                "stock_id": "2330",
                "name": "台積電",
                "score": 92.5,
                "trend_template_pass": 1,
                "is_vcp": 1,
                "details": {"stage": "Stage 2", "contractions": 3, "pivot": 970.0},
            }
        ]
        db.save_scan_results(scan_results)

        cursor = db.conn.execute("SELECT * FROM scan_results WHERE stock_id = '2330'")
        row = cursor.fetchone()
        assert row is not None
        assert row["scan_date"] == "2026-08-19"
        assert row["score"] == 92.5
        details = json.loads(row["details"])
        assert details["stage"] == "Stage 2"
        assert details["contractions"] == 3


def test_db_manager_datetime_index():
    """測試以 DatetimeIndex 作為索引之 DataFrame 寫入."""
    with DBManager(":memory:") as db:
        df = pd.DataFrame(
            {
                "Open": [100.0, 102.0],
                "High": [105.0, 106.0],
                "Low": [99.0, 101.0],
                "Close": [103.0, 105.0],
                "Adj Close": [103.0, 105.0],
                "Volume": [10000, 12000],
            },
            index=pd.to_datetime(["2026-08-18", "2026-08-19"]),
        )
        db.upsert_price_history("1101", df)
        assert db.get_latest_price_date("1101") == "2026-08-19"
        df_read = db.get_price_history("1101")
        assert len(df_read) == 2
