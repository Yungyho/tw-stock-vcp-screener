"""上市/上櫃差異化參數與大盤基準選擇測試 (Market Differentiation Criteria Tests)."""

import pytest
from unittest.mock import patch, MagicMock
import os


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """確保每個測試前後清除 settings 快取."""
    from config.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestGetMarketCriteria:
    """測試 Settings.get_market_criteria() 回傳正確的市場專屬門檻."""

    def test_listed_criteria_returns_twse_defaults(self):
        """上市 (listed) 應回傳 TWSE 預設門檻."""
        from config.settings import get_settings
        settings = get_settings()
        criteria = settings.get_market_criteria("listed")

        assert criteria["min_volume"] == settings.MIN_VOLUME
        assert criteria["min_price"] == settings.MIN_PRICE
        assert criteria["min_market_cap"] == settings.MIN_MARKET_CAP
        assert criteria["min_turnover"] == settings.MIN_TURNOVER_TWD
        assert criteria["min_beta"] == settings.MIN_BETA
        assert criteria["benchmark_symbol"] == settings.BENCHMARK_LISTED
        assert "TAIEX" in criteria["benchmark_name"]

    def test_otc_criteria_returns_tpex_defaults(self):
        """上櫃 (otc) 應回傳 TPEx 專屬寬鬆門檻."""
        from config.settings import get_settings
        settings = get_settings()
        criteria = settings.get_market_criteria("otc")

        assert criteria["min_volume"] == settings.MIN_VOLUME_OTC
        assert criteria["min_price"] == settings.MIN_PRICE_OTC
        assert criteria["min_market_cap"] == settings.MIN_MARKET_CAP_OTC
        assert criteria["min_turnover"] == settings.MIN_TURNOVER_TWD_OTC
        assert criteria["min_beta"] == settings.MIN_BETA_OTC
        assert criteria["benchmark_symbol"] == settings.BENCHMARK_OTC
        assert "TPEx" in criteria["benchmark_name"]

    def test_otc_thresholds_lower_than_listed(self):
        """上櫃門檻應低於上市門檻 (股本與流動性較小)."""
        from config.settings import get_settings
        settings = get_settings()
        listed = settings.get_market_criteria("listed")
        otc = settings.get_market_criteria("otc")

        assert otc["min_volume"] < listed["min_volume"], "OTC 均量門檻應低於上市"
        assert otc["min_price"] < listed["min_price"], "OTC 最低股價應低於上市"
        assert otc["min_market_cap"] < listed["min_market_cap"], "OTC 市值門檻應低於上市"

    def test_unknown_market_defaults_to_listed(self):
        """未知市場代碼應回退至上市 (listed) 門檻."""
        from config.settings import get_settings
        settings = get_settings()
        criteria = settings.get_market_criteria("unknown")

        assert criteria["min_volume"] == settings.MIN_VOLUME
        assert criteria["benchmark_symbol"] == settings.BENCHMARK_LISTED

    def test_benchmark_symbols_are_valid(self):
        """確認大盤基準代號設定正確."""
        from config.settings import get_settings
        settings = get_settings()

        assert settings.BENCHMARK_LISTED == "^TWII"
        assert settings.BENCHMARK_OTC == "006201.TWO"

    def test_otc_env_override(self):
        """確認環境變數可覆寫上櫃門檻."""
        from config.settings import get_settings
        get_settings.cache_clear()

        with patch.dict(os.environ, {
            "MIN_VOLUME_OTC": "500",
            "MIN_PRICE_OTC": "25",
            "MIN_MARKET_CAP_OTC": "2000000000",
        }):
            settings = get_settings()
            criteria = settings.get_market_criteria("otc")

            assert criteria["min_volume"] == 500
            assert criteria["min_price"] == 25.0
            assert criteria["min_market_cap"] == 2000000000.0


class TestBenchmarkRouting:
    """測試大盤基準路由邏輯 (上市 vs 上櫃選擇正確的大盤)."""

    def test_listed_stock_uses_taiex(self):
        """上市股票應使用 TAIEX (^TWII) 作為大盤基準."""
        from config.settings import get_settings
        settings = get_settings()
        criteria = settings.get_market_criteria("listed")
        assert criteria["benchmark_symbol"] == "^TWII"

    def test_otc_stock_uses_tpex(self):
        """上櫃股票應使用 TPEx (006201.TWO) 作為大盤基準."""
        from config.settings import get_settings
        settings = get_settings()
        criteria = settings.get_market_criteria("otc")
        assert criteria["benchmark_symbol"] == "006201.TWO"
