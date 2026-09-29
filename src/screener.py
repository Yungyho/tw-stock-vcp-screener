"""主篩選引擎模組 (Main Screening Engine Module).

此模組整合資料獲取、Trend Template 篩選、VCP 型態偵測與評分排序，
提供完整的 VCP Stage 2 選股掃描流程。
"""

import logging
from datetime import datetime
from typing import Any
from typing import Any, Optional

import pandas as pd

from config.settings import get_settings, Settings
from src.stock_list import fetch_stock_list
from src.trend_template import check_trend_template
from src.beta import calculate_beta
from src.vcp_detector import detect_vcp
from src.scorer import calculate_score, rank_results
from src.data_fetcher import DataFetcher
from src.db.manager import DBManager
from src.market_cap import fetch_and_update_market_caps
from src.notifier.telegram_bot import TelegramNotifier
from src.disposition import DispositionManager

logger = logging.getLogger(__name__)


class VCPScreener:
    """VCP 選股掃描器核心類別 (VCP Stock Screener Core Engine)."""

    def __init__(
        self,
        db_manager: DBManager,
        data_fetcher: DataFetcher,
        notifier: Optional[TelegramNotifier] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.db = db_manager
        self.fetcher = data_fetcher
        self.notifier = notifier
        self.settings = settings or get_settings()

    def _prepare_df_for_analysis(self, df: pd.DataFrame) -> pd.DataFrame:
        """轉換資料庫讀出的 DataFrame 欄位名稱以相容分析模組."""
        col_map = {
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "adj_close": "Adj Close",
            "volume": "Volume",
        }
        return df.rename(columns=col_map)

    def run(
        self,
        skip_vcp: bool = False,
        skip_tt: bool = False,
        limit: Optional[int] = None,
        force_fetch: bool = False,
        market: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """執行完整的 VCP Stage 2 選股掃描流程.

        Args:
            skip_vcp: 是否跳過 VCP 嚴格驗證 (用於測試或寬鬆模式)
            skip_tt: 是否跳過 Trend Template 檢測 (用於測試)
            limit: 限制回傳結果數量
            force_fetch: 是否強制重新自網路下載價格資料 (略過快取)
            market: 指定市場篩選 ('listed' 僅上市 / 'otc' 僅上櫃 / 'all' 全部)

        Returns:
            list[dict]: 排序後的篩選結果清單
        """
        scan_date = datetime.now().strftime("%Y-%m-%d")
        market_filter = (market or self.settings.MARKET).strip().lower()
        scan_mode = (mode or getattr(self.settings, "VCP_SCAN_MODE", "standard")).strip().lower()

        # Step 1: 更新股票清單
        logger.info("Step 1: 正在更新股票清單...")
        try:
            stock_list = fetch_stock_list()
            self.db.upsert_stock_list(stock_list)
            logger.info("股票清單更新完成，共 %d 檔", len(stock_list))
        except Exception as e:
            logger.warning("股票清單更新失敗，使用資料庫中的既有清單: %s", e)

        # Step 2: 從資料庫取得所有股票
        stocks = self.db.get_all_stocks()
        total_stocks = len(stocks)
        logger.info("Step 2: 從資料庫取得 %d 檔股票 (市場設定: %s)", total_stocks, market_filter)

        if not stocks:
            logger.error("股票清單為空，無法執行掃描")
            return []

        # Step 2b: 更新總市值快取 (若開啟市值篩選)
        if self.settings.ENABLE_MARKET_CAP_FILTER:
            logger.info("Step 2b: 檢查並更新股票總市值資料...")
            try:
                fetch_and_update_market_caps(self.db, stocks, force_update=force_fetch)
                # 重新讀取附帶市值的股票清單
                stocks = self.db.get_all_stocks()
            except Exception as e:
                logger.warning("更新股票總市值失敗，將使用既有數據: %s", e)

        # Step 3: 下載/更新歷史股價資料與雙大盤基準 (TAIEX + TPEx)
        logger.info("Step 3: 正在下載/更新歷史股價資料 (force_fetch=%s)...", force_fetch)
        self.fetcher.fetch_all(stocks, days=350, force_fetch=force_fetch)

        # 載入上市大盤基準 (TAIEX ^TWII)
        benchmark_listed_symbol = self.settings.BENCHMARK_LISTED
        benchmark_listed_df = self.db.get_price_history(benchmark_listed_symbol, days=350)
        if benchmark_listed_df.empty:
            logger.info("下載上市大盤基準 (%s) 資料以供 Beta 計算...", benchmark_listed_symbol)
            benchmark_listed_df = self.fetcher.fetch_benchmark(benchmark_listed_symbol)
        if not benchmark_listed_df.empty:
            benchmark_listed_df = self._prepare_df_for_analysis(benchmark_listed_df)

        # 載入上櫃大盤基準 (TPEx 006201.TWO)
        benchmark_otc_symbol = self.settings.BENCHMARK_OTC
        benchmark_otc_df = self.db.get_price_history(benchmark_otc_symbol, days=350)
        if benchmark_otc_df.empty:
            logger.info("下載上櫃大盤基準 (%s) 資料以供 Beta 計算...", benchmark_otc_symbol)
            benchmark_otc_df = self.fetcher.fetch_benchmark(benchmark_otc_symbol)
        if not benchmark_otc_df.empty:
            benchmark_otc_df = self._prepare_df_for_analysis(benchmark_otc_df)

        # Step 3b: 同步並載入處置與注意股票清單 (Disposition & Attention Stocks)
        logger.info("Step 3b: 檢查並同步台股處置與注意股票名單 (TWSE + TPEx)...")
        disp_mgr = DispositionManager(self.db)
        try:
            disp_mgr.sync_to_database()
        except Exception as e:
            logger.warning("同步處置股票名單失敗，將使用資料庫既有快取: %s", e)
        active_dispositions = disp_mgr.get_active_dispositions()
        recent_attentions = disp_mgr.get_recent_attention_stocks(days=3)
        logger.info("當前處於處置期之股票共 %d 檔，近期注意股 %d 檔", len(active_dispositions), len(recent_attentions))

        # Step 4: 逐檔篩選 (依市場別套用差異化門檻)
        logger.info(
            "Step 4: 開始逐檔篩選 (市場: %s, 上市門檻: 市值>%.1f億/均量>%d張, 上櫃門檻: 市值>%.1f億/均量>%d張)...",
            market_filter,
            self.settings.MIN_MARKET_CAP / 1e8,
            self.settings.MIN_VOLUME,
            self.settings.MIN_MARKET_CAP_OTC / 1e8,
            self.settings.MIN_VOLUME_OTC,
        )
        results: list[dict[str, Any]] = []
        stats = {
            "total": total_stocks,
            "data_sufficient": 0,
            "passed_market_cap": 0,
            "passed_volume": 0,
            "disposition_protected": 0,
            "passed_turnover": 0,
            "passed_beta": 0,
            "passed_prefilter": 0,
            "passed_tt": 0,
            "passed_vcp": 0,
        }

        for idx, stock in enumerate(stocks):
            stock_id = stock["stock_id"]
            name = stock["name"]
            stock_market = stock.get("market", "")
            market_cap = float(stock.get("market_cap", 0.0))

            # 市場別過濾
            if market_filter == "listed" and stock_market != "listed":
                continue
            elif market_filter == "otc" and stock_market != "otc":
                continue

            # 取得該股所屬市場的篩選門檻與大盤基準
            criteria = self.settings.get_market_criteria(stock_market)
            benchmark_df = benchmark_listed_df if stock_market != "otc" else benchmark_otc_df

            # 4a: 總市值門檻過濾 (例如 > 5 Billion TWD = 50 億)
            if self.settings.ENABLE_MARKET_CAP_FILTER and market_cap > 0:
                if market_cap < criteria["min_market_cap"]:
                    continue
            stats["passed_market_cap"] += 1

            if (idx + 1) % 200 == 0 or idx == 0:
                logger.info(
                    "Processing stock %d/%d: %s %s (%s)",
                    idx + 1, total_stocks, stock_id, name, stock_market,
                )

            # 4b: 從資料庫讀取歷史價格
            df = self.db.get_price_history(stock_id, days=350)
            if df.empty or len(df) < 252:
                continue
            stats["data_sufficient"] += 1

            # 4c: 成交量、成交金額與最低股價過濾
            disp_info = active_dispositions.get(stock_id)
            attn_info = recent_attentions.get(stock_id)

            recent_20 = df.tail(20)
            avg_volume = float(recent_20["volume"].mean())  # 張數
            last_close = float(df.iloc[-1]["close"])
            turnover_twd = avg_volume * 1000.0 * last_close  # 每日成交金額 (元)

            # 處置股動態均量保護：若處置前常態均量達標，則不因分盤撮合量縮而誤殺
            eval_volume = avg_volume
            eval_turnover_twd = turnover_twd
            is_disp_protected = False

            if disp_info:
                disp_start = disp_info.get("start_date", "")
                if disp_start:
                    pre_df = df[df["date"] < disp_start]
                    if len(pre_df) >= 20:
                        pre_vol = float(pre_df.tail(20)["volume"].mean())
                        if pre_vol > criteria["min_volume"]:
                            eval_volume = pre_vol
                            eval_turnover_twd = pre_vol * 1000.0 * last_close
                            is_disp_protected = True
                            stats["disposition_protected"] += 1
                            logger.info(
                                "🚨 處置股 [%s %s] 啟動常態均量保護: 處置期均量 %.0f 張, 入處置前 20 日均量 %.0f 張 (通過門檻)",
                                stock_id, name, avg_volume, eval_volume,
                            )

            if eval_volume <= criteria["min_volume"]:
                continue
            stats["passed_volume"] += 1

            # 成交金額門檻 (例如 > 100 K TWD = 10 萬元)
            if self.settings.ENABLE_TURNOVER_FILTER and eval_turnover_twd < criteria["min_turnover"]:
                continue
            stats["passed_turnover"] += 1

            if last_close <= criteria["min_price"]:
                continue
            stats["passed_prefilter"] += 1

            # 4d: 轉換欄位名稱以符合分析模組需求
            analysis_df = self._prepare_df_for_analysis(df)

            # 4e: 1 年期 Beta 值過濾 (依市場所屬大盤 > MIN_BETA)
            beta_1y = None
            if not benchmark_df.empty:
                beta_1y = calculate_beta(analysis_df, benchmark_df, lookback_days=252)

            if self.settings.ENABLE_BETA_FILTER and beta_1y is not None:
                if beta_1y < criteria["min_beta"]:
                    continue
            stats["passed_beta"] += 1

            # 4f: Trend Template Stage 2 檢測
            tt_result = check_trend_template(analysis_df)
            if not skip_tt:
                # 必須滿足 Stage 2 核心不可妥協條件
                if not tt_result.get("is_stage_2_core", False):
                    continue
                if tt_result["score"] < self.settings.TREND_TEMPLATE_MIN_PASS:
                    continue
            stats["passed_tt"] += 1

            # 4g: VCP 波動收斂型態偵測 (帶入處置資訊)
            vcp_result = detect_vcp(
                analysis_df,
                strict_mode=self.settings.VCP_STRICT_MODE,
                strict_convergence=self.settings.VCP_STRICT_CONVERGENCE,
                max_tightness=self.settings.VCP_MAX_TIGHTNESS,
                max_pivot_distance=self.settings.VCP_MAX_PIVOT_DISTANCE,
                max_base_depth=self.settings.VCP_MAX_BASE_DEPTH,
                scan_mode=scan_mode,
                include_breakout=getattr(self.settings, "INCLUDE_RECENT_BREAKOUT", True),
                include_retest=getattr(self.settings, "INCLUDE_PIVOT_RETEST", True),
                disposition_info=disp_info,
            )
            if not skip_vcp and not vcp_result["is_vcp"]:
                continue
            stats["passed_vcp"] += 1

            # 4h: 計算綜合評分 (處置股結合抗跌姿態評估)
            score = calculate_score(
                trend_result=tt_result,
                vcp_result=vcp_result,
                df=analysis_df,
                market_df=benchmark_df if not benchmark_df.empty else None,
                disposition_info=disp_info,
            )

            # 計算量縮百分比
            if len(df) >= 50:
                recent_vol = df["volume"].iloc[-20:].mean()
                past_vol = df["volume"].iloc[-50:].mean()
                volume_change = ((recent_vol - past_vol) / past_vol * 100) if past_vol > 0 else 0
            else:
                volume_change = 0

            # 彙整結果
            result_item = {
                "stock_id": stock_id,
                "name": name,
                "close": last_close,
                "market_cap": market_cap,
                "turnover_twd": turnover_twd,
                "beta_1y": beta_1y,
                "benchmark_name": criteria["benchmark_name"],
                "score": round(score, 1),
                "rank": 0,
                "trend_template_pass": tt_result["score"],
                "is_vcp": 1 if vcp_result.get("is_vcp") else 0,
                "action_stage": vcp_result.get("action_stage", "UNCONFIRMED"),
                "action_stage_desc": vcp_result.get("action_stage_desc", ""),
                "contractions": vcp_result.get("contractions", []),
                "tightness": round(vcp_result.get("tightness", 0.0), 2),
                "pivot_price": round(vcp_result.get("pivot_price", last_close), 2),
                "distance_to_pivot": round(vcp_result.get("distance_to_pivot", 0.0), 2),
                "volume_change": round(volume_change, 1),
                "scan_date": scan_date,
                "disposition_info": disp_info,
                "attention_info": attn_info,
                "is_disposition_protected": is_disp_protected,
                "details": {
                    "trend_template": tt_result.get("conditions", {}),
                    "vcp": {
                        "contractions": vcp_result.get("contractions", []),
                        "tightness": vcp_result.get("tightness", 0.0),
                        "volume_declining": vcp_result.get("volume_declining", False),
                        "action_stage": vcp_result.get("action_stage", "UNCONFIRMED"),
                        "action_stage_desc": vcp_result.get("action_stage_desc", ""),
                    },
                    "disposition": disp_info,
                    "attention": attn_info,
                },
            }
            results.append(result_item)

        # Step 5: 排序與儲存
        results = rank_results(results)

        if limit and limit > 0:
            results = results[:limit]
            for r_idx, r in enumerate(results, 1):
                r["rank"] = r_idx

        if results:
            self.db.save_scan_results(results)

        logger.info(
            "\n"
            "==================== 📊 選股漏斗統計 (Funnel Analytics) ====================\n"
            "  1. 股票清單總數:                   %d 檔\n"
            "  2. 歷史數據充足 (>=252天):          %d 檔\n"
            "  3. 通過均量門檻 (上市>%d/上櫃>%d張): %d 檔 (其中處置股均量豁免保護: %d 檔)\n"
            "  4. 通過最低股價 (上市>%d/上櫃>%d元): %d 檔\n"
            "  5. 通過趨勢模板 (>=%d項):           %d 檔\n"
            "  6. 符合 VCP 波動收斂型態:           %d 檔\n"
            "  7. 最終回傳符合標的:               %d 檔\n"
            "==========================================================================",
            stats["total"],
            stats["data_sufficient"],
            self.settings.MIN_VOLUME,
            self.settings.MIN_VOLUME_OTC,
            stats["passed_volume"],
            stats["disposition_protected"],
            int(self.settings.MIN_PRICE),
            int(self.settings.MIN_PRICE_OTC),
            stats["passed_prefilter"],
            self.settings.TREND_TEMPLATE_MIN_PASS,
            stats["passed_tt"],
            stats["passed_vcp"],
            len(results),
        )

        return results
