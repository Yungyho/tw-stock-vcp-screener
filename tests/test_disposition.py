"""處置股票與注意股票功能單元測試 (Unit Tests for Disposition & Attention Module)."""

import unittest
from datetime import date, datetime, timedelta
import pandas as pd
import numpy as np

from src.disposition import roc_to_ad, calculate_remaining_trading_days, DispositionManager
from src.db.manager import DBManager
from src.scorer import calculate_score
from src.vcp_detector import detect_vcp


class TestDispositionModule(unittest.TestCase):
    """測試處置與注意股票功能."""

    def test_roc_to_ad(self):
        """測試民國年轉換為西元 YYYY-MM-DD."""
        self.assertEqual(roc_to_ad("115/09/18"), "2026-09-18")
        self.assertEqual(roc_to_ad("115.09.18"), "2026-09-18")
        self.assertEqual(roc_to_ad("115-09-18"), "2026-09-18")
        self.assertEqual(roc_to_ad("1150918"), "2026-09-18")
        self.assertEqual(roc_to_ad("2026-09-18"), "2026-09-18")
        self.assertEqual(roc_to_ad(""), "")

    def test_calculate_remaining_trading_days(self):
        """測試計算剩餘交易日數 (扣除週六日)."""
        # 假設今日是 2026-09-28 (週一)，結束日是 2026-10-02 (週五) -> 應為 5 個營業日
        rem = calculate_remaining_trading_days("2026-10-02", "2026-09-28")
        self.assertEqual(rem, 5)

        # 跨週末: 2026-09-25 (週五) 到 2026-09-29 (週二) -> 週五、週一、週二 = 3 個營業日
        rem_cross = calculate_remaining_trading_days("2026-09-29", "2026-09-25")
        self.assertEqual(rem_cross, 3)

        # 已過期
        rem_expired = calculate_remaining_trading_days("2026-09-20", "2026-09-25")
        self.assertEqual(rem_expired, 0)

    def test_db_disposition_crud(self):
        """測試 SQLite 資料庫對處置股票的 CRUD 與查詢."""
        with DBManager(":memory:") as db:
            records = [
                {
                    "stock_id": "2305",
                    "stock_name": "全友",
                    "market": "listed",
                    "start_date": "2026-09-18",
                    "end_date": "2026-09-30",
                    "disposition_type": "第一次處置",
                    "matching_interval": "5分撮合",
                    "reasons": "連續五次沖銷標準",
                    "condition_details": "每五分鐘撮合一次",
                },
                {
                    "stock_id": "2455",
                    "stock_name": "全新",
                    "market": "listed",
                    "start_date": "2026-09-23",
                    "end_date": "2026-10-05",
                    "disposition_type": "第二次處置",
                    "matching_interval": "20分撮合",
                    "reasons": "連續注意達標",
                    "condition_details": "每二十分鐘撮合一次",
                },
            ]
            count = db.upsert_disposition_stocks(records)
            self.assertEqual(count, 2)

            # 在處置期間查詢 (2026-09-25)
            active = db.get_active_dispositions("2026-09-25")
            self.assertIn("2305", active)
            self.assertIn("2455", active)
            self.assertEqual(active["2305"]["matching_interval"], "5分撮合")
            self.assertEqual(active["2455"]["matching_interval"], "20分撮合")

            # 結束後查詢 (2026-10-01)
            active_oct = db.get_active_dispositions("2026-10-01")
            self.assertNotIn("2305", active_oct)
            self.assertIn("2455", active_oct)

    def test_adaptive_volume_protection_logic(self):
        """測試處置股動態均量保護邏輯."""
        # 模擬一檔在 2026-09-15 進入處置的股票
        # 入處置前 20 天均量 2,500 張；入處置後（9/15~9/29）日均量僅 350 張
        dates = pd.date_range("2026-01-01", "2026-09-25", freq="B")
        n = len(dates)

        # 建立價格與成交量 (處置自 2026-09-01 開始，共約 18 個交易日)
        disp_start = "2026-09-01"
        close_prices = np.linspace(100, 150, n)
        volumes = []
        for d in dates:
            d_str = d.strftime("%Y-%m-%d")
            if d_str >= disp_start:
                volumes.append(350)  # 處置期間分盤撮合量縮
            else:
                volumes.append(2500)  # 正常期間均量

        df = pd.DataFrame({
            "date": [d.strftime("%Y-%m-%d") for d in dates],
            "open": close_prices,
            "high": close_prices * 1.02,
            "low": close_prices * 0.98,
            "close": close_prices,
            "volume": volumes,
        })

        min_volume_threshold = 1000  # 上市門檻 1000 張

        # 若未進行處置保護，直接看近 20 天均量：
        # 近 20 天幾乎都是處置日 (350張)，均量會跌破 1000
        recent_20_vol = df.tail(20)["volume"].mean()
        self.assertLess(recent_20_vol, min_volume_threshold)

        # 啟用處置保護：回溯入處置前 20 日均量
        pre_df = df[df["date"] < disp_start]
        pre_20_vol = pre_df.tail(20)["volume"].mean()

        # 入處置前均量應大於 1000 張 (2500張)，成功獲得保護豁免
        self.assertGreaterEqual(pre_20_vol, min_volume_threshold)
        self.assertEqual(pre_20_vol, 2500)

    def test_scorer_with_disposition(self):
        """測試 scorer 在處置股情境下的抗跌計分加權."""
        dates = pd.date_range("2026-01-01", "2026-09-25", freq="B")
        n = len(dates)
        # 模擬一檔強勢處置股：股價在 20MA 之上持續盤整
        close_prices = np.full(n, 100.0)
        close_prices[-1] = 105.0  # 高於 20MA
        volumes = np.full(n, 1000)
        volumes[-10:] = 200  # 處置量縮

        df = pd.DataFrame({
            "Date": dates,
            "Open": close_prices,
            "High": close_prices * 1.01,
            "Low": close_prices * 0.99,
            "Close": close_prices,
            "Volume": volumes,
        })

        trend_res = {"score": 9, "is_stage_2_core": True}
        vcp_res = {
            "is_vcp": True,
            "num_contractions": 3,
            "tightness": 5.0,
            "pivot_price": 100.0,
            "distance_to_pivot": 1.0,
        }
        disp_info = {
            "stock_id": "2305",
            "matching_interval": "5分撮合",
            "remaining_trading_days": 2,
        }

        score = calculate_score(trend_res, vcp_res, df, disposition_info=disp_info)
        # 應取得高分 (VCP滿分、趨勢滿分、處置抗跌鎖碼加分)
        self.assertGreater(score, 80.0)


if __name__ == "__main__":
    unittest.main()
