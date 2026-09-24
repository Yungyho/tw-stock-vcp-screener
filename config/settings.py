"""專案設定模組 (Configuration Settings Module).

此模組負責讀取環境變數與 .env 檔案，提供型別轉換與集中式設定管理。
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# 取得專案根目錄路徑
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _parse_bool(val: Optional[str], default: bool = False) -> bool:
    """將字串環境變數轉換為布林值 (Convert string environment variable to bool)."""
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "t", "y")


@dataclass(frozen=True)
class Settings:
    """系統設定類別 (System Settings Data Class).

    包含 Telegram 通知、選股篩選門檻、排程以及資料庫路徑等參數。
    """

    # Telegram Bot 設定
    TELEGRAM_BOT_TOKEN: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    # 頻道推播 Chat ID (每日定時掃描報告發送目標)
    TELEGRAM_CHANNEL_CHAT_ID: str = field(
        default_factory=lambda: os.getenv(
            "TELEGRAM_CHANNEL_CHAT_ID",
            os.getenv("TELEGRAM_CHAT_ID", ""),
        )
    )
    # 個人互動 Chat ID (私聊互動、指令接收與個人推播目標)
    TELEGRAM_USER_CHAT_ID: str = field(
        default_factory=lambda: os.getenv(
            "TELEGRAM_USER_CHAT_ID",
            os.getenv("TELEGRAM_CHAT_ID", ""),
        )
    )
    # 相容舊版 TELEGRAM_CHAT_ID
    TELEGRAM_CHAT_ID: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "")
    )

    # 篩選條件 (Screener Criteria)
    MIN_VOLUME: int = field(
        default_factory=lambda: int(os.getenv("MIN_VOLUME", "1000"))
    )
    MIN_PRICE: float = field(
        default_factory=lambda: float(os.getenv("MIN_PRICE", "30.0"))
    )
    MIN_MARKET_CAP: float = field(
        default_factory=lambda: float(os.getenv("MIN_MARKET_CAP", "5000000000.0"))  # 總市值 > 50 億 (5 Billion TWD)
    )
    MIN_TURNOVER_TWD: float = field(
        default_factory=lambda: float(os.getenv("MIN_TURNOVER_TWD", "100000.0"))   # 成交金額 > 10 萬 TWD (100 K TWD)
    )
    MIN_BETA: float = field(
        default_factory=lambda: float(os.getenv("MIN_BETA", "1.0"))                 # 1年期 Beta > 1.0
    )
    ENABLE_MARKET_CAP_FILTER: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("ENABLE_MARKET_CAP_FILTER"), default=True)
    )
    ENABLE_TURNOVER_FILTER: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("ENABLE_TURNOVER_FILTER"), default=True)
    )
    ENABLE_BETA_FILTER: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("ENABLE_BETA_FILTER"), default=True)
    )
    EXCLUDE_ETF: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("EXCLUDE_ETF"), default=True)
    )
    EXCLUDE_KY: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("EXCLUDE_KY"), default=True)
    )
    EXCLUDE_TDR: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("EXCLUDE_TDR"), default=True)
    )
    MARKET: str = field(
        default_factory=lambda: os.getenv("MARKET", "all").strip().lower()
    )
    TREND_TEMPLATE_MIN_PASS: int = field(
        default_factory=lambda: int(os.getenv("TREND_TEMPLATE_MIN_PASS", "6"))
    )
    VCP_SCAN_MODE: str = field(
        default_factory=lambda: os.getenv("VCP_SCAN_MODE", "standard").strip().lower()
    )
    INCLUDE_RECENT_BREAKOUT: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("INCLUDE_RECENT_BREAKOUT"), default=True)
    )
    INCLUDE_PIVOT_RETEST: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("INCLUDE_PIVOT_RETEST"), default=True)
    )
    VCP_STRICT_MODE: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("VCP_STRICT_MODE"), default=False)
    )
    VCP_STRICT_CONVERGENCE: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("VCP_STRICT_CONVERGENCE"), default=True)
    )
    VCP_MAX_TIGHTNESS: float = field(
        default_factory=lambda: float(os.getenv("VCP_MAX_TIGHTNESS", "12.0"))
    )
    VCP_MAX_PIVOT_DISTANCE: float = field(
        default_factory=lambda: float(os.getenv("VCP_MAX_PIVOT_DISTANCE", "8.0"))
    )
    VCP_MAX_BASE_DEPTH: float = field(
        default_factory=lambda: float(os.getenv("VCP_MAX_BASE_DEPTH", "45.0"))
    )

    # ── 上櫃 (OTC / TPEx) 專屬篩選參數 ──
    # 上櫃股本與流動性較小，需獨立寬鬆門檻以捕捉中小型成長飆股
    MIN_VOLUME_OTC: int = field(
        default_factory=lambda: int(os.getenv("MIN_VOLUME_OTC", "300"))
    )
    MIN_PRICE_OTC: float = field(
        default_factory=lambda: float(os.getenv("MIN_PRICE_OTC", "20.0"))
    )
    MIN_MARKET_CAP_OTC: float = field(
        default_factory=lambda: float(os.getenv("MIN_MARKET_CAP_OTC", "1500000000.0"))  # 總市值 > 15 億 TWD
    )
    MIN_TURNOVER_TWD_OTC: float = field(
        default_factory=lambda: float(os.getenv("MIN_TURNOVER_TWD_OTC", "100000.0"))   # 成交金額 > 10 萬 TWD
    )
    MIN_BETA_OTC: float = field(
        default_factory=lambda: float(os.getenv("MIN_BETA_OTC", "1.0"))
    )

    # ── 大盤基準指數設定 (Benchmark Index) ──
    # 上市股票 Beta 參考 TAIEX 加權指數; 上櫃股票 Beta 參考 TPEx 櫃買指數
    BENCHMARK_LISTED: str = field(
        default_factory=lambda: os.getenv("BENCHMARK_LISTED", "^TWII")
    )
    BENCHMARK_OTC: str = field(
        default_factory=lambda: os.getenv("BENCHMARK_OTC", "006201.TWO")
    )

    # 資料下載長度設定 (Data Period)
    DATA_PERIOD: str = field(
        default_factory=lambda: os.getenv("DATA_PERIOD", "18mo")
    )

    # 回測設定 (Backtest Settings)
    BACKTEST_CAPITAL: float = field(
        default_factory=lambda: float(os.getenv("BACKTEST_CAPITAL", "1000000.0"))
    )
    BACKTEST_MAX_POSITIONS: int = field(
        default_factory=lambda: int(os.getenv("BACKTEST_MAX_POSITIONS", "5"))
    )
    BACKTEST_STOP_LOSS: float = field(
        default_factory=lambda: float(os.getenv("BACKTEST_STOP_LOSS", "0.07"))
    )
    BACKTEST_FEE_DISCOUNT: float = field(
        default_factory=lambda: float(os.getenv("BACKTEST_FEE_DISCOUNT", "0.5"))
    )

    # 排程設定 (Scheduler Settings)
    SCAN_HOUR: int = field(
        default_factory=lambda: int(os.getenv("SCAN_HOUR", "17"))
    )
    SCAN_MINUTE: int = field(
        default_factory=lambda: int(os.getenv("SCAN_MINUTE", "0"))
    )
    TIMEZONE: str = field(
        default_factory=lambda: os.getenv("TIMEZONE", "Asia/Taipei")
    )

    # 資料庫與路徑設定 (Database & Path Settings)
    PROJECT_ROOT: Path = PROJECT_ROOT
    DB_PATH: Path = field(
        default_factory=lambda: (
            Path(os.getenv("DB_PATH"))
            if os.getenv("DB_PATH")
            else PROJECT_ROOT / "data" / "stocks.db"
        )
    )
    LOG_LEVEL: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper()
    )

    def get_market_criteria(self, market: str) -> dict:
        """根據 'listed' 或 'otc' 回傳該市場專屬的篩選門檻與大盤代號.

        Args:
            market: 市場類別 ('listed' 上市 / 'otc' 上櫃)

        Returns:
            dict: 包含 min_volume, min_price, min_market_cap, min_turnover,
                  min_beta, benchmark_symbol, benchmark_name
        """
        if market == "otc":
            return {
                "min_volume": self.MIN_VOLUME_OTC,
                "min_price": self.MIN_PRICE_OTC,
                "min_market_cap": self.MIN_MARKET_CAP_OTC,
                "min_turnover": self.MIN_TURNOVER_TWD_OTC,
                "min_beta": self.MIN_BETA_OTC,
                "benchmark_symbol": self.BENCHMARK_OTC,
                "benchmark_name": "TPEx 櫃買指數",
            }
        else:
            return {
                "min_volume": self.MIN_VOLUME,
                "min_price": self.MIN_PRICE,
                "min_market_cap": self.MIN_MARKET_CAP,
                "min_turnover": self.MIN_TURNOVER_TWD,
                "min_beta": self.MIN_BETA,
                "benchmark_symbol": self.BENCHMARK_LISTED,
                "benchmark_name": "TAIEX 加權指數",
            }


@lru_cache(maxsize=1)
def get_settings(env_file: Optional[Path | str] = None) -> Settings:
    """取得設定單例物件 (Get singleton Settings instance).

    Args:
        env_file: 自訂 .env 檔案路徑，若未提供則預設尋找專案根目錄的 .env

    Returns:
        Settings: 設定實例
    """
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=True)
    else:
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)
        else:
            load_dotenv(override=False)

    return Settings()
