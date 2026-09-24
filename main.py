"""台股 VCP Stage 2 選股系統 — 統一服務入口 (Unified Service Entry Point).

同時啟動：
1. APScheduler 定時排程 (每日 17:00 自動掃描)
2. Telegram Bot 長輪詢指令監聽 (接收 /scan, /analyze 等指令)

用法：
    python main.py
"""

import logging
import os
import signal
import sys
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import get_settings
from src.data_fetcher import DataFetcher
from src.db.manager import DBManager
from src.notifier.telegram_bot import TelegramNotifier
from src.notifier.telegram_command_handler import TelegramCommandBot
from src.screener import VCPScreener


def setup_logging():
    """設定日誌格式與輸出."""
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, "screener.log")

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def run_scan():
    """執行一次全市場掃描任務 (定時排程呼叫，推播至頻道)."""
    settings = get_settings()
    try:
        with DBManager(settings.DB_PATH) as db:
            fetcher = DataFetcher(db)
            target_chat = settings.TELEGRAM_CHANNEL_CHAT_ID or settings.TELEGRAM_CHAT_ID
            notifier = TelegramNotifier(settings.TELEGRAM_BOT_TOKEN, target_chat)
            screener = VCPScreener(db, fetcher, notifier)
            results = screener.run()

            scan_date = datetime.now().strftime("%Y-%m-%d %H:%M")
            notifier.send_scan_report(results, scan_date)

            # 計算全市場寬度指標 (50MA / 200MA) 並繪製折線圖推播
            from src.market_breadth import calculate_market_breadth, get_market_breadth_summary
            from src.reporter.chart_plotter import plot_market_breadth
            from src.reporter.console_reporter import print_market_breadth_report

            breadth_df = calculate_market_breadth(db, days=250, market=settings.MARKET)
            chart_path = plot_market_breadth(breadth_df)
            breadth_summary = get_market_breadth_summary(breadth_df)
            print_market_breadth_report(breadth_summary, chart_path)

            if chart_path:
                notifier.send_photo(chart_path, caption=breadth_summary["caption"])

            logging.info("定時掃描完成，共 %d 檔符合條件 (已推播至 %s)", len(results), target_chat)
    except Exception as e:
        logging.error("定時掃描失敗: %s", e, exc_info=True)
        # 嘗試發送錯誤通知
        try:
            target_chat = settings.TELEGRAM_CHANNEL_CHAT_ID or settings.TELEGRAM_CHAT_ID
            notifier = TelegramNotifier(settings.TELEGRAM_BOT_TOKEN, target_chat)
            notifier.send_text(f"❌ 定時掃描失敗\n\n錯誤: {str(e)}")
        except Exception:
            pass


def main():
    setup_logging()
    settings = get_settings()

    logging.info("=" * 60)
    logging.info("台股 VCP Stage 2 選股系統啟動")
    logging.info("=" * 60)
    logging.info(
        "定時排程: 每週一至週五 %02d:%02d (%s)",
        settings.SCAN_HOUR,
        settings.SCAN_MINUTE,
        settings.TIMEZONE,
    )
    if settings.TELEGRAM_CHANNEL_CHAT_ID:
        logging.info("頻道推播目標 (Channel): %s", settings.TELEGRAM_CHANNEL_CHAT_ID)
    if settings.TELEGRAM_USER_CHAT_ID:
        logging.info("個人互動目標 (User): %s", settings.TELEGRAM_USER_CHAT_ID)

    # ── 1. 啟動 APScheduler 定時排程 (背景模式) ──
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_scan,
        CronTrigger(
            day_of_week="mon-fri",
            hour=settings.SCAN_HOUR,
            minute=settings.SCAN_MINUTE,
            timezone=settings.TIMEZONE,
        ),
        id="vcp_scan",
        name="VCP Stage 2 Daily Scan",
    )
    scheduler.start()
    logging.info("APScheduler 定時排程已啟動")

    # ── 2. 啟動 Telegram Bot 指令監聽 ──
    has_tg = bool(
        settings.TELEGRAM_BOT_TOKEN
        and (settings.TELEGRAM_CHANNEL_CHAT_ID or settings.TELEGRAM_USER_CHAT_ID or settings.TELEGRAM_CHAT_ID)
    )

    if has_tg:
        logging.info("正在啟動 Telegram Bot 指令監聽...")

        bot = TelegramCommandBot(
            token=settings.TELEGRAM_BOT_TOKEN,
            channel_chat_id=settings.TELEGRAM_CHANNEL_CHAT_ID,
            user_chat_id=settings.TELEGRAM_USER_CHAT_ID,
            settings=settings,
        )

        # 發送啟動通知給頻道 (若無頻道則發送給個人)
        notify_target = settings.TELEGRAM_CHANNEL_CHAT_ID or settings.TELEGRAM_USER_CHAT_ID or settings.TELEGRAM_CHAT_ID
        if notify_target:
            try:
                notifier = TelegramNotifier(settings.TELEGRAM_BOT_TOKEN, notify_target)
                notifier.send_text(
                    "🟢 *台股 VCP 分析系統已啟動*\n\n"
                    f"⏰ 每日排程: {settings.SCAN_HOUR:02d}:{settings.SCAN_MINUTE:02d}\n"
                    "📋 輸入 /help 查看可用指令"
                )
            except Exception as e:
                logging.warning("啟動通知發送失敗: %s", e)

        # Bot 的 run_polling 會阻塞主線程，所以在主線程中執行
        try:
            bot.run()
        except (KeyboardInterrupt, SystemExit):
            logging.info("收到停止信號，正在關閉服務...")
            scheduler.shutdown(wait=False)
            logging.info("服務已停止")
    else:
        logging.warning("未設定 Telegram 相關設定，僅啟動排程模式")
        logging.info("按 Ctrl+C 停止服務")
        try:
            # 沒有 TG Bot 時，使用 Event 阻塞主線程
            stop_event = threading.Event()

            def signal_handler(signum, frame):
                stop_event.set()

            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
            stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            scheduler.shutdown(wait=False)
            logging.info("服務已停止")


if __name__ == "__main__":
    main()
