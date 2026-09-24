"""全市場寬度指標與圖表生成單元測試 (Market Breadth Unit Tests)."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from src.market_breadth import calculate_market_breadth, get_market_breadth_summary
from src.reporter.chart_plotter import plot_market_breadth


@pytest.fixture
def mock_breadth_df():
    """建立模擬的市場寬度時間序列 DataFrame."""
    dates = pd.date_range("2025-09-01", periods=100, freq="B").strftime("%Y-%m-%d")
    df = pd.DataFrame(
        {
            "pct_above_50ma": np.linspace(35, 65, 100),
            "pct_above_200ma": np.linspace(40, 55, 100),
            "total_stocks_50ma": [1800] * 100,
            "total_stocks_200ma": [1780] * 100,
            "benchmark_close": np.linspace(21000, 24000, 100),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


class TestMarketBreadthSummary:
    """測試市場寬度摘要產生與解讀邏輯."""

    def test_summary_with_data(self, mock_breadth_df):
        """測試摘要正確提取最新值、變動幅度與產生 Caption."""
        summary = get_market_breadth_summary(mock_breadth_df)

        assert summary["date"] == mock_breadth_df.index[-1]
        assert summary["pct_50"] == pytest.approx(65.0, 0.1)
        assert summary["pct_200"] == pytest.approx(55.0, 0.1)
        assert summary["sample_stocks"] == 1800
        assert "全市場寬度指標報告" in summary["caption"]
        assert "50MA" in summary["caption"]
        assert "200MA" in summary["caption"]

    def test_summary_with_empty_df(self):
        """測試空 DataFrame 之優雅降級處理."""
        empty_df = pd.DataFrame()
        summary = get_market_breadth_summary(empty_df)

        assert summary["date"] == "N/A"
        assert summary["pct_50"] == 0.0
        assert summary["pct_200"] == 0.0
        assert "數據不足" in summary["market_regime"]

    def test_regime_classification(self):
        """測試不同數值區間的市場結構解讀判定."""
        # 1. 強勢全面多頭 (過熱)
        df_bull = pd.DataFrame(
            {"pct_above_50ma": [75.0], "pct_above_200ma": [65.0], "total_stocks_50ma": [100]},
            index=["2026-09-01"],
        )
        s1 = get_market_breadth_summary(df_bull)
        assert "強勢全面多頭" in s1["market_regime"]

        # 2. 恐慌超賣
        df_oversold = pd.DataFrame(
            {"pct_above_50ma": [18.0], "pct_above_200ma": [25.0], "total_stocks_50ma": [100]},
            index=["2026-09-01"],
        )
        s2 = get_market_breadth_summary(df_oversold)
        assert "恐慌超賣" in s2["market_regime"]


class TestMarketBreadthPlotter:
    """測試折線圖繪製模組."""

    def test_plot_market_breadth_generates_file(self, mock_breadth_df, tmp_path):
        """測試順利繪製折線圖並輸出實體 PNG 檔案."""
        out_png = tmp_path / "test_breadth.png"
        saved_path = plot_market_breadth(mock_breadth_df, output_path=str(out_png))

        assert saved_path is not None
        assert os.path.exists(saved_path)
        assert os.path.getsize(saved_path) > 1000  # 圖檔大小正常

    def test_plot_market_breadth_empty_df(self, tmp_path):
        """測試傳入空 DataFrame 時優雅回傳 None."""
        out_png = tmp_path / "test_empty.png"
        saved_path = plot_market_breadth(pd.DataFrame(), output_path=str(out_png))
        assert saved_path is None
