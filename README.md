# 🔍 台股 VCP Stage 2 選股掃描器

自動掃描全台股，篩選出處於 **Mark Minervini VCP（Volatility Contraction Pattern）第二階段上升趨勢**的個股，並透過 **Telegram Bot** 定期推播通知。

## ✨ 功能特色

- 📊 **完整 Stage 2 Trend Template**：實作 Minervini 9 條選股條件
- 📉 **VCP 波動收斂偵測**：自動辨識連續收縮型態
- 🏆 **綜合評分排序**：依趨勢品質、收斂程度、量能萎縮、距離突破點等維度評分
- 🤖 **Telegram 自動通知**：每日收盤後推播篩選結果
- 🗃️ **本地資料庫快取**：SQLite 快取避免重複下載

## 🚀 快速開始

### 1. 環境準備

```bash
# 建立虛擬環境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 安裝依賴
pip install -r requirements.txt
```

### 2. 設定 Telegram Bot

1. 在 Telegram 搜尋 **@BotFather**，發送 `/newbot` 建立機器人
2. 記下 Bot 的 **API Token**
3. 對你的 Bot 發送任意訊息
4. 開啟 `https://api.telegram.org/bot<TOKEN>/getUpdates` 取得你的 **Chat ID**

### 3. 建立環境變數

```bash
# 複製 .env 範本
copy .env.example .env

# 編輯 .env，填入你的 Telegram Token 和 Chat ID
```

`.env` 檔案內容：
```env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGhIjKlMnOpQrStUvWxYz
TELEGRAM_CHAT_ID=123456789

MIN_VOLUME=5000
MIN_PRICE=50
EXCLUDE_ETF=true
EXCLUDE_KY=true
EXCLUDE_TDR=true
TREND_TEMPLATE_MIN_PASS=9

SCAN_HOUR=17
SCAN_MINUTE=0
TIMEZONE=Asia/Taipei
```

### 4. 執行

```bash
# 手動單次執行（測試用）
python run_once.py

# 手動執行但不發送通知
python run_once.py --no-notify

# 啟動排程（每個交易日 17:00 自動執行）
python main.py
```

## 📁 專案結構

```
tw-stock-vcp-screener/
├── .env.example              # 環境變數範本
├── .gitignore
├── requirements.txt
├── README.md
├── main.py                   # 主程式（含 APScheduler 排程）
├── run_once.py               # 手動單次執行
├── run_scan.bat              # Windows Task Scheduler 批次檔
├── config/
│   └── settings.py           # 設定管理
├── data/
│   └── stocks.db             # SQLite 資料庫（自動建立）
├── logs/
│   └── screener.log          # 執行日誌
├── src/
│   ├── stock_list.py         # 台股清單爬取
│   ├── data_fetcher.py       # 歷史股價下載
│   ├── trend_template.py     # Stage 2 條件檢測
│   ├── vcp_detector.py       # VCP 型態辨識
│   ├── screener.py           # 主篩選引擎
│   ├── scorer.py             # 綜合評分
│   ├── notifier/
│   │   ├── base.py           # 通知介面
│   │   └── telegram_bot.py   # Telegram 通知
│   └── db/
│       └── manager.py        # 資料庫管理
└── tests/
    ├── test_scaffold.py
    └── test_stock_list_and_fetcher.py
```

## ⏰ Windows Task Scheduler 設定

1. 開啟「工作排程器」（Task Scheduler）
2. 建立基本工作 → 觸發程序設為「每日 17:00」
3. 動作 → 啟動程式 → 選擇 `run_scan.bat`
4. 起始位置設為專案根目錄

## 🔧 篩選條件說明

### Trend Template（9 條件全部通過）

| 條件 | 說明 |
|:---|:---|
| 股價 > SMA150 | 中期趨勢向上 |
| 股價 > SMA200 | 長期趨勢向上 |
| SMA150 > SMA200 | 均線多頭排列 |
| SMA200 上升 ≥ 1 月 | 長期趨勢持續 |
| SMA50 > SMA150 | 短中期多頭排列 |
| SMA50 > SMA200 | 短長期多頭排列 |
| 股價 > SMA50 | 短期趨勢向上 |
| 距 52 週低 ≥ 25% | 已脫離底部 |
| 距 52 週高 ≤ 25% | 接近前高 |

### VCP 型態條件

- 至少 2 段波動收縮，每段振幅遞減
- 成交量逐段萎縮
- 價格接近突破點

### 前置過濾

- 日均量 > 5,000 張
- 股價 > 50 元
- 排除 ETF、KY 股、存託憑證

## 📝 License

MIT
