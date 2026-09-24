import argparse
import logging
import sys
import os
from datetime import datetime

from config.settings import get_settings
from src.db.manager import DBManager
from src.data_fetcher import DataFetcher
from src.notifier.telegram_bot import TelegramNotifier  
from src.screener import VCPScreener

def setup_logging():
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
        
    log_file = os.path.join(log_dir, "screener_manual.log")
    
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

def main():
    parser = argparse.ArgumentParser(description="Manual single-run script for VCP screener")
    parser.add_argument("--no-notify", action="store_true", help="Skip sending notification")
    parser.add_argument("--skip-vcp", action="store_true", help="Skip VCP validation to allow testing notifications")
    parser.add_argument("--skip-tt", action="store_true", help="Skip Trend Template validation")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of output results (e.g. 5)")
    parser.add_argument("--test-mode", action="store_true", help="Test mode: skip VCP check and output top 5 stocks for TG notification test")
    parser.add_argument("--force-fetch", "--force", action="store_true", help="Force re-fetch all historical price data bypassing daily cache")
    parser.add_argument("--clear-today", action="store_true", help="Clear only today's cached price data from DB before scanning")
    parser.add_argument("--clear-cache", action="store_true", help="Clear all stored price history in DB before scanning")
    parser.add_argument("--market", choices=["all", "listed", "otc"], default=None, help="Filter market: 'listed' (上市 only), 'otc' (上櫃 only), or 'all'")
    parser.add_argument("--listed-only", action="store_true", help="Shortcut for --market listed (only scan TWSE listed stocks)")
    parser.add_argument("--mode", choices=["standard", "loose", "strict"], default=None, help="VCP screening mode: standard (default, 12%%), loose (15%%), or strict (8%%)")
    parser.add_argument("--loose", action="store_true", help="Shortcut for --mode loose")
    parser.add_argument("--strict", action="store_true", help="Shortcut for --mode strict")
    parser.add_argument("--breadth-only", action="store_true", help="Only compute market breadth indicators (>50MA & >200MA) and generate chart without running full stock scan")
    args = parser.parse_args()

    setup_logging()
    settings = get_settings()
    market = "listed" if args.listed_only else args.market
    mode = "strict" if args.strict else ("loose" if args.loose else args.mode)

    try:
        with DBManager(settings.DB_PATH) as db:
            fetcher = DataFetcher(db)
            notifier = TelegramNotifier(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)

            # ── 模式 A: 僅計算並輸出市場寬度折線圖 ──
            if args.breadth_only:
                logging.info("執行市場寬度指標計算 (50MA / 200MA)...")
                from src.market_breadth import calculate_market_breadth, get_market_breadth_summary
                from src.reporter.chart_plotter import plot_market_breadth
                from src.reporter.console_reporter import print_market_breadth_report

                breadth_df = calculate_market_breadth(db, days=250, market=market)
                chart_path = plot_market_breadth(breadth_df)
                summary = get_market_breadth_summary(breadth_df)
                print_market_breadth_report(summary, chart_path)

                if not args.no_notify and chart_path:
                    notifier.send_photo(chart_path, caption=summary["caption"])
                return

            # ── 模式 B: 完整 VCP 選股掃描 ──
            logging.info('Starting manual VCP scan (mode=%s)...', mode or getattr(settings, 'VCP_SCAN_MODE', 'standard'))
            if args.clear_today:
                logging.info("清除今日 SQLite 價格快取中...")
                db.clear_today_cache()
            if args.clear_cache:
                logging.info("清除 SQLite 歷史價格快取中...")
                db.clear_cache(clear_prices=True, clear_stocks=False, clear_results=False)

            # 測試模式自動開啟 skip_vcp 並限制 5 筆
            skip_vcp = args.skip_vcp or args.test_mode
            skip_tt = args.skip_tt
            limit = args.limit if args.limit is not None else (5 if args.test_mode else None)
            force_fetch = args.force_fetch or args.clear_cache or args.clear_today

            screener = VCPScreener(db, fetcher, notifier)
            results = screener.run(
                skip_vcp=skip_vcp,
                skip_tt=skip_tt,
                limit=limit,
                force_fetch=force_fetch,
                market=market,
                mode=mode,
            )
            
            scan_date = datetime.now().strftime('%Y-%m-%d')
            logging.info(f'Scan completed. {len(results)} stocks found.')

            # ── 掃描完成後：計算全市場寬度指標 (50MA/200MA) 並繪製折線圖 ──
            logging.info("正在計算全市場寬度指標 (50MA / 200MA) 並輸出折線圖...")
            from src.market_breadth import calculate_market_breadth, get_market_breadth_summary
            from src.reporter.chart_plotter import plot_market_breadth
            from src.reporter.console_reporter import print_market_breadth_report

            breadth_df = calculate_market_breadth(db, days=250, market=market)
            chart_path = plot_market_breadth(breadth_df)
            breadth_summary = get_market_breadth_summary(breadth_df)
            print_market_breadth_report(breadth_summary, chart_path)
            
            if not args.no_notify:
                # 1. 發送選股清單推播報告
                notifier.send_scan_report(results, scan_date)
                # 2. 發送全市場寬度折線圖推播
                if chart_path:
                    notifier.send_photo(chart_path, caption=breadth_summary["caption"])
            else:
                logging.info('Skipping notification as requested.')
                
    except Exception as e:
        logging.error(f'Scan failed: {e}', exc_info=True)
        if not args.no_notify:
            try:
                notifier = TelegramNotifier(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
                notifier.send_text(f'❌ 掃描失敗\n\n錯誤: {str(e)}')
            except:
                pass

if __name__ == "__main__":
    main()
