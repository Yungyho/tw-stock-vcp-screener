"""股票清單獲取與價格下載模組單元測試 (Unit tests for stock_list and data_fetcher)."""

from datetime import datetime
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from src.db.manager import DBManager
from src.data_fetcher import DataFetcher
from src.stock_list import _parse_isin_table, fetch_stock_list


SAMPLE_LISTED_HTML = """
<html>
<body>
<table class="h4" border="0" width="100%" cellpadding="2" cellspacing="1">
    <tr>
        <td bgcolor="#D5FFD5">有價證券代號及名稱</td>
        <td bgcolor="#D5FFD5">國際證券辨識號碼(ISIN Code)</td>
        <td bgcolor="#D5FFD5">上市日</td>
        <td bgcolor="#D5FFD5">市場別</td>
        <td bgcolor="#D5FFD5">有價證券別</td>
        <td bgcolor="#D5FFD5">產業別</td>
        <td bgcolor="#D5FFD5">CFI Code</td>
        <td bgcolor="#D5FFD5">備註</td>
    </tr>
    <tr><td colspan="7" bgcolor="#FAFAD2"><b> 股票 </b></td></tr>
    <!-- 正常普通股 -->
    <tr>
        <td bgcolor="#FAFAD2">1101　台泥</td>
        <td bgcolor="#FAFAD2">TW0001101004</td>
        <td bgcolor="#FAFAD2">1962/02/09</td>
        <td bgcolor="#FAFAD2">上市</td>
        <td bgcolor="#FAFAD2">股票</td>
        <td bgcolor="#FAFAD2">水泥工業</td>
        <td bgcolor="#FAFAD2">ESVUFR</td>
        <td bgcolor="#FAFAD2"></td>
    </tr>
    <tr>
        <td bgcolor="#FAFAD2">2330　台積電</td>
        <td bgcolor="#FAFAD2">TW0002330008</td>
        <td bgcolor="#FAFAD2">1994/09/05</td>
        <td bgcolor="#FAFAD2">上市</td>
        <td bgcolor="#FAFAD2">股票</td>
        <td bgcolor="#FAFAD2">半導體業</td>
        <td bgcolor="#FAFAD2">ESVUFR</td>
        <td bgcolor="#FAFAD2"></td>
    </tr>
    <!-- 排除: 00 開頭 ETF (即使有價證券別寫股票) -->
    <tr>
        <td bgcolor="#FAFAD2">0050　元大台灣50</td>
        <td bgcolor="#FAFAD2">TW0000050004</td>
        <td bgcolor="#FAFAD2">2003/06/30</td>
        <td bgcolor="#FAFAD2">上市</td>
        <td bgcolor="#FAFAD2">股票</td>
        <td bgcolor="#FAFAD2"></td>
        <td bgcolor="#FAFAD2">CEOGEU</td>
        <td bgcolor="#FAFAD2"></td>
    </tr>
    <!-- 排除: KY 股 -->
    <tr>
        <td bgcolor="#FAFAD2">6538　倉和-KY</td>
        <td bgcolor="#FAFAD2">TW0006538002</td>
        <td bgcolor="#FAFAD2">2016/10/28</td>
        <td bgcolor="#FAFAD2">上市</td>
        <td bgcolor="#FAFAD2">股票</td>
        <td bgcolor="#FAFAD2">光電業</td>
        <td bgcolor="#FAFAD2">ESVUFR</td>
        <td bgcolor="#FAFAD2"></td>
    </tr>
    <!-- 排除: DR / 存託憑證 -->
    <tr>
        <td bgcolor="#FAFAD2">9105　泰金寶-DR</td>
        <td bgcolor="#FAFAD2">TW0009105007</td>
        <td bgcolor="#FAFAD2">2009/10/08</td>
        <td bgcolor="#FAFAD2">上市</td>
        <td bgcolor="#FAFAD2">股票</td>
        <td bgcolor="#FAFAD2">其他業</td>
        <td bgcolor="#FAFAD2">EDSDTR</td>
        <td bgcolor="#FAFAD2"></td>
    </tr>
    <tr>
        <td bgcolor="#FAFAD2">9103　美德向邦存託憑證</td>
        <td bgcolor="#FAFAD2">TW0009103002</td>
        <td bgcolor="#FAFAD2">2002/12/13</td>
        <td bgcolor="#FAFAD2">上市</td>
        <td bgcolor="#FAFAD2">股票</td>
        <td bgcolor="#FAFAD2">其他業</td>
        <td bgcolor="#FAFAD2">EDSDTR</td>
        <td bgcolor="#FAFAD2"></td>
    </tr>
    <!-- 排除: 特別股或權證含英文字母 -->
    <tr>
        <td bgcolor="#FAFAD2">2881A　富邦金甲特</td>
        <td bgcolor="#FAFAD2">TW0002881A01</td>
        <td bgcolor="#FAFAD2">2016/06/22</td>
        <td bgcolor="#FAFAD2">上市</td>
        <td bgcolor="#FAFAD2">股票</td>
        <td bgcolor="#FAFAD2">金融保險業</td>
        <td bgcolor="#FAFAD2">EPVUFR</td>
        <td bgcolor="#FAFAD2"></td>
    </tr>
    <!-- 排除: 非股票類別 -->
    <tr>
        <td bgcolor="#FAFAD2">03001P　國泰01購01</td>
        <td bgcolor="#FAFAD2">TW00003001P8</td>
        <td bgcolor="#FAFAD2">2020/01/01</td>
        <td bgcolor="#FAFAD2">上市</td>
        <td bgcolor="#FAFAD2">認購售權證</td>
        <td bgcolor="#FAFAD2"></td>
        <td bgcolor="#FAFAD2">RWSUFR</td>
        <td bgcolor="#FAFAD2"></td>
    </tr>
</table>
</body>
</html>
"""

SAMPLE_OTC_HTML = """
<html>
<body>
<table class="h4" border="0" width="100%" cellpadding="2" cellspacing="1">
    <tr>
        <td bgcolor="#D5FFD5">有價證券代號及名稱</td>
        <td bgcolor="#D5FFD5">國際證券辨識號碼(ISIN Code)</td>
        <td bgcolor="#D5FFD5">上櫃日</td>
        <td bgcolor="#D5FFD5">市場別</td>
        <td bgcolor="#D5FFD5">有價證券別</td>
        <td bgcolor="#D5FFD5">產業別</td>
        <td bgcolor="#D5FFD5">CFI Code</td>
        <td bgcolor="#D5FFD5">備註</td>
    </tr>
    <tr>
        <td bgcolor="#FAFAD2">6488　環球晶</td>
        <td bgcolor="#FAFAD2">TW0006488000</td>
        <td bgcolor="#FAFAD2">2015/09/25</td>
        <td bgcolor="#FAFAD2">上櫃</td>
        <td bgcolor="#FAFAD2">股票</td>
        <td bgcolor="#FAFAD2">半導體業</td>
        <td bgcolor="#FAFAD2">ESVUFR</td>
        <td bgcolor="#FAFAD2"></td>
    </tr>
    <tr>
        <td bgcolor="#FAFAD2">3293　鈊象</td>
        <td bgcolor="#FAFAD2">TW0003293006</td>
        <td bgcolor="#FAFAD2">2006/07/12</td>
        <td bgcolor="#FAFAD2">上櫃</td>
        <td bgcolor="#FAFAD2">股票</td>
        <td bgcolor="#FAFAD2">文化創意業</td>
        <td bgcolor="#FAFAD2">ESVUFR</td>
        <td bgcolor="#FAFAD2"></td>
    </tr>
</table>
</body>
</html>
"""


def test_parse_isin_table_listed():
    """測試上市股票 ISIN 表格解析與各項過濾條件."""
    stocks = _parse_isin_table(SAMPLE_LISTED_HTML, market="listed")
    assert len(stocks) == 2

    # 驗證台泥 1101
    s0 = stocks[0]
    assert s0["stock_id"] == "1101"
    assert s0["name"] == "台泥"
    assert s0["market"] == "listed"
    assert s0["yf_symbol"] == "1101.TW"

    # 驗證台積電 2330
    s1 = stocks[1]
    assert s1["stock_id"] == "2330"
    assert s1["name"] == "台積電"
    assert s1["market"] == "listed"
    assert s1["yf_symbol"] == "2330.TW"


def test_parse_isin_table_otc():
    """測試上櫃股票 ISIN 表格解析與 .TWO 代碼後綴."""
    stocks = _parse_isin_table(SAMPLE_OTC_HTML, market="otc")
    assert len(stocks) == 2

    assert stocks[0]["stock_id"] == "6488"
    assert stocks[0]["name"] == "環球晶"
    assert stocks[0]["market"] == "otc"
    assert stocks[0]["yf_symbol"] == "6488.TWO"

    assert stocks[1]["stock_id"] == "3293"
    assert stocks[1]["name"] == "鈊象"
    assert stocks[1]["market"] == "otc"
    assert stocks[1]["yf_symbol"] == "3293.TWO"


@patch("src.stock_list._fetch_html_with_retry")
def test_fetch_stock_list_combined(mock_fetch):
    """測試 fetch_stock_list 整合上市與上櫃股票清單."""
    mock_fetch.side_effect = [SAMPLE_LISTED_HTML, SAMPLE_OTC_HTML]
    all_stocks = fetch_stock_list()

    assert len(all_stocks) == 4
    stock_ids = [s["stock_id"] for s in all_stocks]
    assert stock_ids == ["1101", "2330", "6488", "3293"]
    assert all_stocks[0]["yf_symbol"] == "1101.TW"
    assert all_stocks[2]["yf_symbol"] == "6488.TWO"


def test_data_fetcher_clean_and_convert_df():
    """測試 DataFetcher 成交量換算 (股轉張) 與欄位標準化."""
    with DBManager(":memory:") as db:
        fetcher = DataFetcher(db)

        # 模擬 yfinance 回傳數據 (Volume 單位為股)
        raw_df = pd.DataFrame(
            {
                "Open": [900.0, 910.0],
                "High": [920.0, 930.0],
                "Low": [890.0, 900.0],
                "Close": [915.0, 925.0],
                "Adj Close": [915.0, 925.0],
                "Volume": [10000000, 25500000],  # 10,000,000 股 = 10,000 張
            },
            index=pd.to_datetime(["2026-08-18", "2026-08-19"]),
        )

        cleaned_df = fetcher._clean_and_convert_df(raw_df)
        assert cleaned_df is not None
        assert list(cleaned_df.columns) == ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
        # 驗證成交量是否已除以 1000
        assert list(cleaned_df["Volume"]) == [10000, 25500]


@patch("yfinance.download")
def test_data_fetcher_fetch_single(mock_yf_download):
    """測試 fetch_single 單檔個股下載與欄位對齊."""
    mock_df = pd.DataFrame(
        {
            "Open": [500.0],
            "High": [520.0],
            "Low": [490.0],
            "Close": [510.0],
            "Adj Close": [510.0],
            "Volume": [5000000],
        },
        index=pd.to_datetime(["2026-08-19"]),
    )
    mock_yf_download.return_value = mock_df

    with DBManager(":memory:") as db:
        fetcher = DataFetcher(db)
        df = fetcher.fetch_single("2330.TW")

        assert not df.empty
        assert len(df) == 1
        assert df.iloc[0]["Close"] == 510.0
        assert df.iloc[0]["Volume"] == 5000  # 5,000,000 / 1000 = 5000 張


@patch("yfinance.download")
def test_data_fetcher_fetch_all_with_caching(mock_yf_download):
    """測試 fetch_all 之本地快取跳過與批次下載寫入資料庫."""
    with DBManager(":memory:") as db:
        today_str = datetime.now().strftime("%Y-%m-%d")

        # 預先在 DB 寫入 2330 的今日資料
        cached_df = pd.DataFrame(
            {
                "date": [today_str],
                "open": [950.0],
                "high": [960.0],
                "low": [940.0],
                "close": [955.0],
                "adj_close": [955.0],
                "volume": [30000],
            }
        )
        db.upsert_price_history("2330", cached_df)

        # 模擬 yfinance 批次下載回傳 2454.TW
        idx = pd.to_datetime([today_str])
        cols = pd.MultiIndex.from_tuples(
            [
                ("2454.TW", "Open"),
                ("2454.TW", "High"),
                ("2454.TW", "Low"),
                ("2454.TW", "Close"),
                ("2454.TW", "Adj Close"),
                ("2454.TW", "Volume"),
            ]
        )
        data = [[1200.0, 1250.0, 1190.0, 1240.0, 1240.0, 8000000]]
        batch_return_df = pd.DataFrame(data, index=idx, columns=cols)
        mock_yf_download.return_value = batch_return_df

        fetcher = DataFetcher(db)
        stocks_input = [
            {"stock_id": "2330", "name": "台積電", "market": "listed", "yf_symbol": "2330.TW"},
            {"stock_id": "2454", "name": "聯發科", "market": "listed", "yf_symbol": "2454.TW"},
        ]

        results = fetcher.fetch_all(stocks_input)

        # 驗證兩檔股票都有結果
        assert "2330" in results
        assert "2454" in results

        # 驗證 2330 來自 DB 快取
        assert len(results["2330"]) == 1
        assert results["2330"].iloc[0]["close"] == 955.0

        # 驗證 2454 經由 yf.download 取得並寫入 DB
        assert db.get_latest_price_date("2454") == today_str
        df_2454_db = db.get_price_history("2454")
        assert len(df_2454_db) == 1
        assert df_2454_db.iloc[0]["close"] == 1240.0
        assert df_2454_db.iloc[0]["volume"] == 8000  # 8000000 / 1000 = 8000 張
