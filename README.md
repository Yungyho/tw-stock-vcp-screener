# 🔍 台股 VCP Stage 2 選股掃描器 (Taiwan Stock VCP Screener)

自動掃描全台股（上市與上櫃），篩選出處於 **Mark Minervini VCP（Volatility Contraction Pattern）第二階段上升趨勢** 的潛力個股，並透過 **Telegram Bot** 提供每日定時推播與即時互動診斷。

---

## ✨ 功能特色

- 📊 **完整 Stage 2 Trend Template**：實作 Minervini 9 條選股條件檢測（支援 9 條嚴格全過或自訂放寬）。
- 📉 **VCP 波動收斂型態偵測**：
  - 支援 **標準 (`standard`)**、**寬鬆 (`loose`)**、**嚴格 (`strict`)** 三種掃描模式。
  - 支援剛突破（1~3日內漲幅 0~8%）與回踩樞紐點（Pivot Retest）偵測。
  - 嚴格要求逐波振幅收窄 ($T_1 > T_2 > T_3$)、底底高與量能收縮。
- 📈 **市場寬度指標 (Market Breadth)**：
  - 統計近一年每日全市場股價高於 50MA 與 200MA 的比例。
  - 自動生成市場寬度折線圖，協助判斷大盤處於多頭攻擊、廣泛拉回或多空轉折期。
- 🏛️ **上市 / 上櫃 (OTC) 分級門檻**：
  - 針對上市與上櫃股票設定不同的市值、成交量與股價門檻。
  - 分別對標 **TAIEX 加權指數 (`^TWII`)** 與 **TPEx 櫃買指數 (`006201.TWO`)** 計算專屬 Beta 值。
- 🤖 **Telegram 互動式機器人**：
  - 每日 17:00 收盤後自動向頻道推播選股結果與市場寬度圖表。
  - 支援私聊或頻道下達指令：
    - `/scan [mode]`：手動觸發掃描（例如 `/scan loose`）。
    - `/analyze <代號>`：無論是否符合 VCP，皆進行完整的階段判斷（第 1~4 階段）與 VCP 結構診斷。
    - `/breadth`：即時產出最新市場寬度圖表與統計報告。
    - `/help`：查看指令說明。
- 🗃️ **本地資料庫快取**：SQLite 自動快取歷史股價，大幅加速後續重複掃描速度。

---

## 🚀 快速開始

### 1. 安裝環境

```bash
# 建立並啟用虛擬環境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 安裝依賴套件
pip install -r requirements.txt
```

### 2. 設定 Telegram Bot

1. 在 Telegram 搜尋 **@BotFather**，發送 `/newbot` 建立機器人並取得 **API Token**。
2. 取得您的 Chat ID：
   - **頻道推播**：將 Bot 加入您的公開或私人頻道並設為管理員，取得頻道 ID（如 `-1001234567890`）。
   - **個人互動**：私訊 **@userinfobot** 或 **@getmyid_bot** 取得個人的 User ID。

### 3. 設定環境變數 (`.env`)

複製範本檔案並根據您的需求編輯：

```bash
copy .env.example .env        # Windows
# cp .env.example .env        # macOS / Linux
```

#### `.env` 完整設定說明

```env
# ============================================================
# Telegram Bot 設定
# ============================================================
TELEGRAM_BOT_TOKEN=your_bot_token_here

# 1. 頻道推播 Chat ID (每日 17:00 定時選股報告發送目標)
TELEGRAM_CHANNEL_CHAT_ID=-1001234567890

# 2. 個人互動 Chat ID (私聊下指令與接收個人專屬通知)
TELEGRAM_USER_CHAT_ID=

# 3. 兼容舊版設定
TELEGRAM_CHAT_ID=-1001234567890

# ============================================================
# 市場篩選 (listed: 僅看上市 / otc: 僅看上櫃 / all: 上市+上櫃)
# ============================================================
MARKET=all

# 資料歷史下載長度 (18mo: 1.5年約350交易日 / 2y: 2年 / 5y: 5年)
DATA_PERIOD=18mo

# ── 篩選條件 (上市 / Listed) ──
MIN_VOLUME=1000               # 近20日均量門檻 (張)
MIN_PRICE=30                  # 最低股價門檻 (元)
MIN_MARKET_CAP=5000000000     # 總市值門檻 > 50 億台幣
MIN_TURNOVER_TWD=100000       # 每日成交金額門檻 > 10 萬台幣
MIN_BETA=1.0                  # 1 年期相對於大盤之 Beta 值 > 1.0

# ── 上櫃 (OTC / TPEx) 專屬篩選門檻 ──
MIN_VOLUME_OTC=300            # 近20日均量門檻 (張) — 上櫃
MIN_PRICE_OTC=20              # 最低股價門檻 (元) — 上櫃
MIN_MARKET_CAP_OTC=1500000000 # 總市值門檻 > 15 億台幣 — 上櫃
MIN_TURNOVER_TWD_OTC=100000   # 每日成交金額門檻 > 10 萬台幣 — 上櫃
MIN_BETA_OTC=1.0              # 1 年期相對於櫃買指數之 Beta 值 > 1.0 — 上櫃

# ── 大盤基準指數 ──
BENCHMARK_LISTED=^TWII        # 上市大盤加權指數
BENCHMARK_OTC=006201.TWO      # 上櫃大盤櫃買指數 (富邦富櫃50 ETF)

# 篩選開關 (true: 開啟 / false: 關閉)
ENABLE_MARKET_CAP_FILTER=true
ENABLE_TURNOVER_FILTER=true
ENABLE_BETA_FILTER=true
EXCLUDE_ETF=true
EXCLUDE_KY=true
EXCLUDE_TDR=true

# Trend Template 門檻 (滿分9條，符合9條即全部通過；亦可設為 6~8 放寬)
TREND_TEMPLATE_MIN_PASS=9

# VCP 波動收斂設定 (模式: standard 標準 / loose 寬鬆 / strict 嚴格)
VCP_SCAN_MODE=standard
INCLUDE_RECENT_BREAKOUT=true  # 納入 1~3 日內剛突破 (0~8%) 之個股
INCLUDE_PIVOT_RETEST=true     # 納入回踩樞紐有守之個股
VCP_STRICT_MODE=false
VCP_STRICT_CONVERGENCE=true   # 嚴格要求逐波振幅收窄 (T1 > T2 > T3) 與底底高
VCP_MAX_TIGHTNESS=12.0        # 最後一段振幅容許至 12% (寬鬆模式自動放寬至 15%)
VCP_MAX_PIVOT_DISTANCE=8.0   # 距離突破點 8% 內 (寬鬆模式自動放寬至 10%)
VCP_MAX_BASE_DEPTH=45.0       # 底部最大回檔容許 45%

# 排程設定
SCAN_HOUR=17
SCAN_MINUTE=0
TIMEZONE=Asia/Taipei
```

---

## 🏃 執行方式

### 1. 啟動 Telegram 機器人常駐服務（含定時排程與指令監聽）

```bash
python main.py
```
> 每天下午 17:00 會自動執行選股掃描、產出市場寬度圖表並推播至設定的 Telegram 頻道。同時常駐監聽 `/scan`、`/analyze`、`/breadth` 指令。

### 2. 單次手動執行掃描

```bash
# 手動單次掃描並發送 Telegram 通知
python run_once.py

# 手動單次掃描，不發送通知（僅終端機輸出）
python run_once.py --no-notify

# 強制重新下載股價資料進行掃描
python run_once.py --force-download
```

### 3. 個股診斷與市場寬度獨立測試

```bash
# 診斷指定股票之 Stage 階段與 VCP 詳細狀態 (例: 台積電 2330)
python -m src.analyzer_core 2330

# 產生最新全市場寬度指標報告與折線圖
python -m src.market_breadth
```

---

## 🔧 篩選邏輯與核心條件

### 1. 前置流動性與市值過濾
| 項目 | 上市門檻 (Listed) | 上櫃門檻 (OTC) |
|:---|:---|:---|
| 最低股價 | $\ge$ 30 元 | $\ge$ 20 元 |
| 20日均量 | $\ge$ 1,000 張 | $\ge$ 300 張 |
| 總市值 | $\ge$ 50 億台幣 | $\ge$ 15 億台幣 |
| 成交金額 | $\ge$ 10 萬台幣 | $\ge$ 10 萬台幣 |
| 1年期 Beta | $\ge$ 1.0 (相對於加權指數) | $\ge$ 1.0 (相對於櫃買指數) |
| 排除標的 | ETF、KY 股、TDR 存託憑證 | ETF、KY 股、TDR 存託憑證 |

### 2. Trend Template（Minervini 9 大多頭標準）
1. 現價 > 150MA 且現價 > 200MA
2. 150MA > 200MA
3. 200MA 呈現上升趨勢（至少持續 1 個月）
4. 50MA > 150MA 且 50MA > 200MA
5. 現價 > 50MA
6. 現價距離 52 週最低點高出至少 25%
7. 現價距離 52 週最高點在 25% 以內
8. 股價處於第二階段（Stage 2）上升趨勢確認

### 3. VCP 波動收斂型態
- **收縮次數**：2 至 4 次收縮（$2T \sim 4T$）。
- **振幅收窄**：前一波振幅大於後一波（$T_1 > T_2 > T_3$），最後一段收斂振幅 $\le 12\%$（寬鬆模式為 $15\%$）。
- **量能萎縮**：收縮期間成交量明顯縮小至 50 日均量之下。
- **樞紐點位置**：距離突破點（Pivot Point）在 $8\%$ 內（寬鬆模式為 $10\%$），或為剛突破 1~3 日內（漲幅 $0 \sim 8\%$）之個股。

---

## 📁 專案結構

```
tw-stock-vcp-screener/
├── .env                      # 實際環境變數（不進 Git）
├── .env.example              # 環境變數設定範本
├── .gitignore                # Git 忽略設定
├── requirements.txt          # 專案依賴套件
├── README.md                 # 專案說明文件
├── main.py                   # Telegram 互動 Bot 與定時排程主入口
├── run_once.py               # 手動單次掃描腳本
├── run_scan.bat              # Windows 工作排程批次檔
├── config/
│   └── settings.py           # 環境變數讀取與集中設定模組
├── data/
│   └── stocks.db             # SQLite 本地股價資料庫（自動建立）
├── logs/
│   └── screener.log          # 執行日誌
└── src/
    ├── analyzer_core.py      # 個股綜合診斷核心
    ├── beta.py               # Beta 值計算模組 (上市/上櫃大盤基準)
    ├── data_fetcher.py       # 歷史股價下載與本地快取
    ├── market_breadth.py     # 市場寬度指標統計與折線圖繪製
    ├── market_cap.py         # 總市值抓取與計算
    ├── scorer.py             # VCP 綜合評分模組
    ├── screener.py           # 選股掃描引擎
    ├── stage_analyzer.py     # Stan Weinstein 股票四階段分析
    ├── stock_list.py         # 台股清單爬取與過濾
    ├── trend_template.py     # Stage 2 趨勢模板檢驗
    ├── vcp_detector.py       # VCP 收斂形態辨識
    ├── notifier/
    │   ├── base.py           # 通知基底類別
    │   └── telegram_bot.py   # Telegram 訊息排版與圖文推播
    └── db/
        └── manager.py        # SQLite 資料庫連線管理
```

---

## 📝 授權條款

MIT License
