"""台灣股市股票清單獲取模組 (Taiwan Stock List Fetcher Module).

此模組負責從台灣證券交易所 (TWSE) 與證券櫃檯買賣中心 (TPEx)
抓取所有上市 (約 900+ 檔) 與上櫃 (約 800+ 檔) 普通股清單，合計約 1,700+ 檔。

採多層級備援架構：
- 上市股票：ISIN 網頁 (strMode=2) -> TWSE 收盤行情 JSON API -> TWSE 每日行情
- 上櫃股票：ISIN 網頁 (strMode=4) -> TPEx OpenAPI -> TPEx 官方即時/收盤行情 JSON API
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

import requests
import urllib3
from bs4 import BeautifulSoup

# 關閉 SSL 驗證警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# ── 主要來源：TWSE ISIN 網頁 ──
URL_ISIN_LISTED = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
URL_ISIN_OTC = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"

# ── 備用來源：TWSE / TPEx JSON API ──
URL_TWSE_JSON = "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json"
URL_TPEX_OPENAPI = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
URL_TPEX_DAILY_JSON = "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&o=json"
URL_TPEX_CLOSE_JSON = "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json"

# HTTP 請求標頭
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _fetch_html_with_retry(
    url: str,
    max_retries: int = 3,
    timeout: int = 45,
) -> str:
    """下載指定網址的 HTML 內容並支援失敗重試."""
    session = requests.Session()
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("正在請求 ISIN 網址 (第 %d/%d 次): %s", attempt, max_retries, url)
            response = session.get(url, headers=REQUEST_HEADERS, timeout=timeout, verify=False)
            response.raise_for_status()

            content_bytes = response.content
            try:
                html_text = content_bytes.decode("cp950")
            except (UnicodeDecodeError, LookupError):
                try:
                    html_text = content_bytes.decode("big5", errors="replace")
                except Exception:
                    html_text = response.text

            return html_text
        except requests.RequestException as e:
            logger.warning("下載 ISIN 網址失敗 (第 %d/%d 次): %s, 錯誤: %s", attempt, max_retries, url, e)
            if attempt < max_retries:
                time.sleep(3 * attempt)

    return ""


def _is_valid_stock(stock_id: str, name: str) -> bool:
    """檢查是否為有效的普通股（排除 ETF、KY 股、TDR 存託憑證、權證等）."""
    stock_id = str(stock_id).strip()
    name = str(name).strip()

    # 必須為 4 位純數字代號
    if not stock_id.isdigit() or len(stock_id) != 4:
        return False

    # 排除 ETF (代號 00 開頭)
    if stock_id.startswith("00"):
        return False

    # 排除 KY 股票（海外註冊）
    if "KY" in name.upper():
        return False

    # 排除 存託憑證 / DR
    if "存託憑證" in name or "DR" in name.upper():
        return False

    return True


def _parse_isin_table(html_content: str, market: str) -> List[Dict[str, str]]:
    """解析 ISIN 表格資料並提取普通股清單."""
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, "html.parser")
    table = soup.find("table", class_="h4") or soup.find("table")
    if not table:
        return []

    rows = table.find_all("tr")
    if not rows:
        return []

    # 動態定位欄位索引
    id_name_idx = 0
    sec_type_idx = -1
    for tr in rows:
        cells = tr.find_all(["td", "th"])
        cell_texts = [c.get_text(strip=True) for c in cells]
        if "有價證券代號及名稱" in cell_texts:
            id_name_idx = cell_texts.index("有價證券代號及名稱")
            if "有價證券別" in cell_texts:
                sec_type_idx = cell_texts.index("有價證券別")
            break

    yf_suffix = ".TW" if market == "listed" else ".TWO"
    stocks: List[Dict[str, str]] = []
    seen_ids = set()
    current_category = ""

    for tr in rows:
        cells = tr.find_all("td")
        if not cells:
            continue

        # 檢查是否為大分類標題列 (例如: "股票", "上市認購(售)權證")
        if len(cells) == 1 or (len(cells) > 0 and cells[0].has_attr("colspan")):
            current_category = cells[0].get_text(strip=True)
            continue

        if len(cells) <= id_name_idx:
            continue

        # 若有「有價證券別」欄位則檢查，若無則依據大分類
        if sec_type_idx != -1 and len(cells) > sec_type_idx:
            sec_type = cells[sec_type_idx].get_text(strip=True)
            if "股票" not in sec_type and "特別股" not in sec_type:
                continue
        else:
            # 依大分類篩選
            if "股票" not in current_category:
                continue

        # 解析代號與名稱
        raw_id_name = cells[id_name_idx].get_text(strip=True)
        parts = re.split(r"[\s\u3000\xa0]+", raw_id_name, maxsplit=1)
        if len(parts) < 2:
            continue

        stock_id = parts[0].strip()
        name = parts[1].strip()

        if not _is_valid_stock(stock_id, name):
            continue

        if stock_id in seen_ids:
            continue
        seen_ids.add(stock_id)

        stocks.append({
            "stock_id": stock_id,
            "name": name,
            "market": market,
            "yf_symbol": f"{stock_id}{yf_suffix}",
        })

    logger.info("ISIN 解析 [%s]: 取得 %d 檔股票", market, len(stocks))
    return stocks


# ──────────────────────────────────────────────
#  上市股票清單獲取 (支援多來源備援)
# ──────────────────────────────────────────────

def _fetch_listed_stocks() -> List[Dict[str, str]]:
    """抓取上市 (TWSE) 股票清單 (預期 ~850-950 檔)."""
    # 1. 優先來源: TWSE ISIN (strMode=2)
    html = _fetch_html_with_retry(URL_ISIN_LISTED)
    stocks = _parse_isin_table(html, market="listed")
    if len(stocks) >= 500:
        return stocks

    # 2. 備用來源: TWSE STOCK_DAY_ALL JSON API
    logger.warning("ISIN 上市股票不足 (%d 檔)，啟用 TWSE JSON API 備援...", len(stocks))
    try:
        resp = requests.get(URL_TWSE_JSON, headers=REQUEST_HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
        data = resp.json()

        json_stocks = []
        seen = set()
        if data.get("stat") == "OK" and "data" in data:
            for row in data["data"]:
                if len(row) < 2:
                    continue
                s_id = str(row[0]).strip()
                s_name = str(row[1]).strip()
                if _is_valid_stock(s_id, s_name) and s_id not in seen:
                    seen.add(s_id)
                    json_stocks.append({
                        "stock_id": s_id,
                        "name": s_name,
                        "market": "listed",
                        "yf_symbol": f"{s_id}.TW",
                    })

        if len(json_stocks) >= 500:
            logger.info("TWSE JSON API 備援成功: 取得 %d 檔上市股票", len(json_stocks))
            return json_stocks
    except Exception as e:
        logger.warning("TWSE JSON API 取得失敗: %s", e)

    return stocks


# ──────────────────────────────────────────────
#  上櫃股票清單獲取 (支援多來源備援)
# ──────────────────────────────────────────────

def _fetch_otc_stocks() -> List[Dict[str, str]]:
    """抓取上櫃 (TPEx) 股票清單 (預期 ~750-850 檔)."""
    # 1. 優先來源: TWSE ISIN (strMode=4)
    html = _fetch_html_with_retry(URL_ISIN_OTC)
    stocks = _parse_isin_table(html, market="otc")
    if len(stocks) >= 400:
        return stocks

    logger.warning("ISIN 上櫃股票不足 (%d 檔)，啟用 TPEx 官方 API 備援...", len(stocks))

    # 2. 備用來源 A: TPEx 每日收盤行情 JSON API
    try:
        resp = requests.get(URL_TPEX_CLOSE_JSON, headers=REQUEST_HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
        data = resp.json()

        # aaData 結構: [[代號, 名稱, 收盤, 漲跌, 開盤, 最高, 最低, 成交股數, ...], ...]
        rows = data.get("aaData", []) or data.get("tables", [{}])[0].get("data", [])
        otc_json_stocks = []
        seen = set()

        for row in rows:
            if len(row) < 2:
                continue
            s_id = str(row[0]).strip()
            s_name = str(row[1]).strip()
            if _is_valid_stock(s_id, s_name) and s_id not in seen:
                seen.add(s_id)
                otc_json_stocks.append({
                    "stock_id": s_id,
                    "name": s_name,
                    "market": "otc",
                    "yf_symbol": f"{s_id}.TWO",
                })

        if len(otc_json_stocks) >= 400:
            logger.info("TPEx 收盤行情 API 備援成功: 取得 %d 檔上櫃股票", len(otc_json_stocks))
            return otc_json_stocks
    except Exception as e:
        logger.warning("TPEx 收盤行情 API 取得失敗: %s", e)

    # 3. 備用來源 B: TPEx OpenAPI
    try:
        resp = requests.get(URL_TPEX_OPENAPI, headers=REQUEST_HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
        items = resp.json()

        otc_api_stocks = []
        seen = set()
        for item in items:
            s_id = str(item.get("SecuritiesCompanyCode", "")).strip()
            s_name = str(item.get("CompanyName", "")).strip()
            if _is_valid_stock(s_id, s_name) and s_id not in seen:
                seen.add(s_id)
                otc_api_stocks.append({
                    "stock_id": s_id,
                    "name": s_name,
                    "market": "otc",
                    "yf_symbol": f"{s_id}.TWO",
                })

        if len(otc_api_stocks) >= 400:
            logger.info("TPEx OpenAPI 備援成功: 取得 %d 檔上櫃股票", len(otc_api_stocks))
            return otc_api_stocks
    except Exception as e:
        logger.warning("TPEx OpenAPI 取得失敗: %s", e)

    return stocks


def _deduplicate(stocks: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """去除重複股票."""
    combined: List[Dict[str, str]] = []
    seen = set()
    for s in stocks:
        s_id = s["stock_id"]
        if s_id not in seen:
            seen.add(s_id)
            combined.append(s)
    return combined


def fetch_stock_list() -> List[Dict[str, str]]:
    """獲取台灣上市與上櫃全部普通股股票清單 (預期合計 1,600~1,800 檔).

    Returns:
        List[Dict[str, str]]: 股票字典清單 (stock_id, name, market, yf_symbol)
    """
    logger.info("開始抓取台灣股市股票清單 (上市 & 上櫃)...")

    # 1. 抓取上市股票 (TWSE)
    listed_stocks = _fetch_listed_stocks()

    # 2. 抓取上櫃股票 (TPEx)
    otc_stocks = _fetch_otc_stocks()

    # 3. 合併與去重
    combined = _deduplicate(listed_stocks + otc_stocks)

    logger.info(
        "股票清單獲取完成: 上市 %d 檔, 上櫃 %d 檔, 總計 %d 檔普通股",
        len(listed_stocks),
        len(otc_stocks),
        len(combined),
    )
    return combined


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
    )
    stocks = fetch_stock_list()
    print(f"總共抓取到 {len(stocks)} 檔股票")
    if stocks:
        print("上市範例:", [s for s in stocks if s["market"] == "listed"][:3])
        print("上櫃範例:", [s for s in stocks if s["market"] == "otc"][:3])
