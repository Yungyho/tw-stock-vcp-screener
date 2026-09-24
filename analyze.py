"""個股 VCP 型態與四大趨勢階段診斷工具 (Stock VCP & 4-Stage Diagnostic CLI).

用法：
    python analyze.py 2330
    python analyze.py 2330 2454 3008
    python analyze.py 2330 --force
"""

import argparse
import logging
import sys

from config.settings import get_settings
from src.analyzer_core import analyze_stock, prepare_benchmark
from src.data_fetcher import DataFetcher
from src.db.manager import DBManager
from src.reporter.console_reporter import print_stock_diagnostic_report


def setup_logging():
    """設定簡潔的終端機日誌."""
    logging.basicConfig(
        level=logging.WARNING,  # 診斷工具保持 Console 清爽，僅顯示 Warning/Error
        format="[%(levelname)s] %(message)s",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Taiwan Stock VCP & 4-Stage Diagnostic Tool (Minervini SEPA)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""範例：
  python analyze.py 2330
  python analyze.py 2330 2454 3008
  python analyze.py 2330 --force
        """,
    )
    parser.add_argument(
        "stocks",
        nargs="+",
        help="One or more Taiwan stock codes to analyze (e.g. 2330, 2454, 6488)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download latest price data and market cap from Yahoo Finance",
    )
    args = parser.parse_args()

    setup_logging()
    settings = get_settings()

    # 展開逗號分隔的代號
    target_stocks = []
    for item in args.stocks:
        for s in item.replace(",", " ").split():
            clean_s = s.strip()
            if clean_s:
                target_stocks.append(clean_s)

    if not target_stocks:
        print("❌ 請指定至少一檔股票代號，例如: python analyze.py 2330")
        sys.exit(1)

    with DBManager(settings.DB_PATH) as db:
        fetcher = DataFetcher(db)

        # 載入上市大盤基準 (TAIEX ^TWII) 與上櫃大盤基準 (TPEx 006201.TWO)
        benchmark_listed_df = prepare_benchmark(
            db, fetcher, symbol=settings.BENCHMARK_LISTED, force_fetch=args.force
        )
        benchmark_otc_df = prepare_benchmark(
            db, fetcher, symbol=settings.BENCHMARK_OTC, force_fetch=args.force
        )

        for stock_code in target_stocks:
            # 先解析股票所屬市場以選擇正確的大盤基準
            from src.analyzer_core import resolve_stock_symbol
            stock_info_preview = resolve_stock_symbol(db, stock_code)
            stock_market = stock_info_preview.get("market", "listed")
            benchmark_df = benchmark_otc_df if stock_market == "otc" else benchmark_listed_df

            result = analyze_stock(
                db=db,
                fetcher=fetcher,
                stock_input=stock_code,
                benchmark_df=benchmark_df,
                force_fetch=args.force,
                settings=settings,
            )
            if result:
                print_stock_diagnostic_report(
                    stock_info=result["stock_info"],
                    df=result["df"],
                    stage_res=result["stage_res"],
                    tt_result=result["tt_result"],
                    vcp_result=result["vcp_result"],
                    score=result["score"],
                    beta_1y=result["beta_1y"],
                    market_cap=result["market_cap"],
                    turnover_twd=result["turnover_twd"],
                    benchmark_name=result.get("benchmark_name"),
                )
            else:
                print(f"\n❌ 無法取得股票代號【{stock_code}】足夠的歷史數據（至少需 252 個交易日），請確認代號是否正確。")


if __name__ == "__main__":
    main()
