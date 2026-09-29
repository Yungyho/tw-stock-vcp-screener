"""Telegram Bot 互動指令處理器 (Telegram Bot Command Handler).

使用 python-telegram-bot v20+ 的長輪詢 (Long Polling) 架構，
持續監聽 Telegram 訊息並處理使用者指令。
支援群組 (Group)、頻道 (Channel Post) 以及一對一私聊 (Private Chat)。

支援指令：
    /scan        - 執行全市場 VCP Stage 2 選股掃描
    /scan force  - 清除今日快取後強制重掃
    /analyze <代號> - 指定個股四階段 + VCP 深度診斷
    /status      - 查看服務狀態
    /help        - 顯示指令說明
"""

import asyncio
import logging
import threading
from datetime import datetime
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config.settings import Settings, get_settings
from src.analyzer_core import analyze_stock, prepare_benchmark
from src.data_fetcher import DataFetcher
from src.db.manager import DBManager
from src.notifier.telegram_bot import TelegramNotifier
from src.reporter.telegram_reporter import format_stock_diagnostic
from src.screener import VCPScreener

logger = logging.getLogger(__name__)


class TelegramCommandBot:
    """Telegram Bot 互動指令處理器.

    在獨立線程中以 Long Polling 方式持續監聽 Telegram 訊息，
    並根據指令觸發全市場掃描或個股診斷分析。
    支援 Channel Posts、Supergroups 與 Private Chats。
    """

    def __init__(
        self,
        token: Optional[str] = None,
        channel_chat_id: Optional[str] = None,
        user_chat_id: Optional[str] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.token = token or self.settings.TELEGRAM_BOT_TOKEN
        self.channel_chat_id = str(channel_chat_id or self.settings.TELEGRAM_CHANNEL_CHAT_ID).strip()
        self.user_chat_id = str(user_chat_id or self.settings.TELEGRAM_USER_CHAT_ID).strip()
        self._scan_lock = asyncio.Lock()
        self._start_time = datetime.now()
        self._last_scan_time: Optional[datetime] = None
        self._is_running = False

    def _is_authorized(self, update: Update) -> bool:
        """驗證訊息來源是否為授權的聊天室或使用者 (支援個人私聊與授權頻道)."""
        if update.effective_chat is None:
            return False
        chat_id_str = str(update.effective_chat.id)
        user_id_str = str(update.effective_user.id) if update.effective_user else ""

        # 1. 匹配個人互動 CHAT_ID 或 User ID
        if self.user_chat_id and (chat_id_str == self.user_chat_id or user_id_str == self.user_chat_id):
            return True

        # 2. 匹配頻道/群組 CHAT_ID
        if self.channel_chat_id and chat_id_str == self.channel_chat_id:
            return True

        # 3. 若為私聊，且尚未設定特定 USER_CHAT_ID 時預設允許
        if update.effective_chat.type == "private" and not self.user_chat_id:
            return True

        return False

    async def _send_reply(self, update: Update, text: str, parse_mode: Optional[str] = None, disable_web_page_preview: bool = True) -> None:
        """統一回覆訊息封裝 (相容 message 與 channel_post)."""
        msg = update.effective_message
        if msg:
            try:
                await msg.reply_text(
                    text,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                )
                return
            except Exception as e:
                logger.warning("以 Markdown 回覆失敗，降級純文字發送: %s", e)
                try:
                    await msg.reply_text(text, disable_web_page_preview=disable_web_page_preview)
                    return
                except Exception:
                    pass

        # 備援：透過 bot.send_message 主動發送至該 chat_id
        if update.effective_chat:
            try:
                await update.get_bot().send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                )
            except Exception as e:
                logger.error("主動發送訊息至 %s 失敗: %s", update.effective_chat.id, e)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """處理 /help 指令 → 顯示所有可用指令說明."""
        if not self._is_authorized(update):
            return

        help_text = (
            "🤖 *台股 VCP Stage 2 分析系統 — 指令說明*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "📊 */scan* — 執行全市場 VCP 選股掃描 (標準模式)\n"
            "   • `/scan loose` : 寬鬆模式 (容許 15% 收縮，捕捉較寬波段)\n"
            "   • `/scan strict`: 嚴格模式 (容許 8% 極致收縮，防守型)\n"
            "   • `/scan force` : 強制清除快取重新自網路抓取\n"
            "   完成後自動回傳符合條件的個股清單與市場寬度折線圖\n\n"
            "📈 */breadth* — 查看全市場寬度指標折線圖\n"
            "   顯示近一年股價站上 50MA 與 200MA 比例及多空結構解讀\n\n"
            "🚨 */disp* (或 */disposition*) — 全台股處置股票名單\n"
            "   查詢目前處於處置中的個股、分盤撮合方式與出關倒數\n\n"
            "🔍 */analyze <代號>* — 個股深度診斷\n"
            "   分析指定個股的四大階段、TT 9 條件、VCP 收斂型態與處置警示\n"
            "   範例: `/analyze 2330`\n"
            "   多檔: `/analyze 2330 2454 3008`\n\n"
            "📋 */status* — 查看服務運行狀態\n"
            "   顯示運行時長、上次掃描時間等\n\n"
            "💡 直接輸入股票代號 (如 `2330`) 也可自動分析！\n\n"
            "❓ */help* — 顯示此說明\n"
        )
        await self._send_reply(update, help_text, parse_mode="Markdown")

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """處理 /start 指令."""
        if not self._is_authorized(update):
            return
        await self.cmd_help(update, context)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """處理 /status 指令 → 回傳服務運行狀態."""
        if not self._is_authorized(update):
            return

        uptime = datetime.now() - self._start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)

        last_scan_str = (
            self._last_scan_time.strftime("%Y-%m-%d %H:%M:%S")
            if self._last_scan_time
            else "尚未執行"
        )

        next_scan = f"每日 {self.settings.SCAN_HOUR:02d}:{self.settings.SCAN_MINUTE:02d} ({self.settings.TIMEZONE})"

        status_text = (
            "📋 *服務運行狀態*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ 狀態: 正常運行中\n"
            f"⏱️ 運行時長: {hours}小時 {minutes}分 {seconds}秒\n"
            f"📅 上次掃描: {last_scan_str}\n"
            f"⏰ 定時排程: {next_scan}\n"
            f"📂 市場篩選: {self.settings.MARKET}\n"
            f"📊 DATA\\_PERIOD: {self.settings.DATA_PERIOD}\n"
        )
        await self._send_reply(update, status_text, parse_mode="Markdown")

    async def cmd_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """處理 /scan 指令 → 執行全市場 VCP 掃描並回傳結果."""
        if not self._is_authorized(update):
            return

        # 防重複掃描
        if self._scan_lock.locked():
            await self._send_reply(update, "⏳ 掃描正在進行中，請稍候完成後再試...")
            return

        async with self._scan_lock:
            # 判斷是否為 force 模式與指定 scan_mode
            force_mode = False
            scan_mode = None
            if context.args:
                for arg in context.args:
                    arg_l = arg.lower()
                    if arg_l == "force":
                        force_mode = True
                    elif arg_l in ("loose", "strict", "standard"):
                        scan_mode = arg_l

            mode_parts = []
            if scan_mode:
                mode_parts.append(f"模式: {scan_mode.upper()}")
            if force_mode:
                mode_parts.append("強制重抓")
            mode_label = f" ({', '.join(mode_parts)})" if mode_parts else ""

            await self._send_reply(
                update,
                f"🔄 正在執行全市場 VCP Stage 2 選股掃描{mode_label}...\n\n⏳ 這可能需要數分鐘，完成後將自動回傳結果。"
            )

            try:
                # 在獨立線程中執行同步掃描與市場寬度計算（避免阻塞 Bot 事件迴圈）
                results, breadth_summary, chart_path = await asyncio.get_event_loop().run_in_executor(
                    None, self._run_scan_sync, force_mode, scan_mode
                )

                self._last_scan_time = datetime.now()
                scan_date = self._last_scan_time.strftime("%Y-%m-%d %H:%M")

                target_chat = str(update.effective_chat.id) if update.effective_chat else self.channel_chat_id
                notifier = TelegramNotifier(self.token, target_chat)

                if results:
                    # 1. 發送選股清單報告至發起指令的聊天室
                    notifier.send_scan_report(results, scan_date)
                else:
                    await self._send_reply(
                        update,
                        f"📊 *VCP Stage 2 掃描完成*\n📅 {scan_date}\n\n❌ 今日無符合條件的個股",
                        parse_mode="Markdown",
                    )

                # 2. 發送全市場寬度折線圖推播
                if chart_path:
                    notifier.send_photo(chart_path, caption=breadth_summary.get("caption", ""))

            except Exception as e:
                logger.error("Telegram /scan 指令執行失敗: %s", e, exc_info=True)
                await self._send_reply(update, f"❌ 掃描失敗\n\n錯誤: {str(e)[:500]}")

    def _run_scan_sync(self, force_mode: bool, scan_mode: Optional[str] = None) -> tuple:
        """同步執行全市場掃描與市場寬度指標運算（在執行緒池中呼叫）."""
        settings = self.settings
        with DBManager(settings.DB_PATH) as db:
            if force_mode:
                db.clear_today_cache()

            fetcher = DataFetcher(db)
            notifier = TelegramNotifier(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
            screener = VCPScreener(db, fetcher, notifier, settings)
            results = screener.run(force_fetch=force_mode, market=settings.MARKET, mode=scan_mode)

            # 計算市場寬度指標並產出折線圖
            from src.market_breadth import calculate_market_breadth, get_market_breadth_summary
            from src.reporter.chart_plotter import plot_market_breadth

            breadth_df = calculate_market_breadth(db, days=250, market=settings.MARKET)
            chart_path = plot_market_breadth(breadth_df)
            breadth_summary = get_market_breadth_summary(breadth_df)

            return results, breadth_summary, chart_path

    async def cmd_breadth(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """處理 /breadth 指令 → 即時計算並回傳全市場寬度指標折線圖."""
        if not self._is_authorized(update):
            return

        await self._send_reply(
            update,
            "📈 正在計算全市場寬度指標 (50MA & 200MA) 並繪製折線圖...\n⏳ 請稍候數秒...",
        )

        try:
            target_chat = str(update.effective_chat.id) if update.effective_chat else self.channel_chat_id
            summary, chart_path = await asyncio.get_event_loop().run_in_executor(
                None, self._run_breadth_sync
            )

            if chart_path:
                notifier = TelegramNotifier(self.token, target_chat)
                notifier.send_photo(chart_path, caption=summary.get("caption", ""))
            else:
                await self._send_reply(update, "❌ 市場寬度圖表生成失敗，請確認歷史數據是否充足。")
        except Exception as e:
            logger.error("Telegram /breadth 指令執行失敗: %s", e, exc_info=True)
            await self._send_reply(update, f"❌ 指令執行失敗: {str(e)[:500]}")

    def _run_breadth_sync(self) -> tuple:
        """同步計算全市場寬度指標與繪製圖表（在執行緒池中呼叫）."""
        from src.market_breadth import calculate_market_breadth, get_market_breadth_summary
        from src.reporter.chart_plotter import plot_market_breadth

        with DBManager(self.settings.DB_PATH) as db:
            breadth_df = calculate_market_breadth(db, days=250, market=self.settings.MARKET)
            chart_path = plot_market_breadth(breadth_df)
            summary = get_market_breadth_summary(breadth_df)
            return summary, chart_path

    async def cmd_analyze(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """處理 /analyze <代號> 指令 → 個股診斷並回傳報告."""
        if not self._is_authorized(update):
            return

        if not context.args or len(context.args) == 0:
            await self._send_reply(
                update,
                "❓ 請指定股票代號\n\n"
                "範例:\n"
                "  `/analyze 2330`\n"
                "  `/analyze 2330 2454 3008`",
                parse_mode="Markdown",
            )
            return

        stock_codes = context.args
        await self._send_reply(
            update,
            f"🔍 正在分析 {', '.join(stock_codes)}...\n⏳ 請稍候..."
        )

        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None, self._run_analyze_sync, stock_codes
            )

            for stock_code, result in results:
                if result:
                    report = format_stock_diagnostic(result)
                    await self._send_reply(update, report, parse_mode="Markdown")
                else:
                    await self._send_reply(
                        update,
                        f"❌ 無法分析【{stock_code}】\n可能原因: 代號錯誤或歷史數據不足 (至少需 252 個交易日)"
                    )
        except Exception as e:
            logger.error("Telegram /analyze 指令執行失敗: %s", e, exc_info=True)
            await self._send_reply(update, f"❌ 分析失敗\n\n錯誤: {str(e)[:500]}")

    def _run_analyze_sync(self, stock_codes: list) -> list:
        """同步執行個股分析（在執行緒池中呼叫）."""
        settings = self.settings
        results = []

        with DBManager(settings.DB_PATH) as db:
            fetcher = DataFetcher(db)
            # 載入上市大盤基準 (TAIEX) 與上櫃大盤基準 (TPEx)
            benchmark_listed_df = prepare_benchmark(
                db, fetcher, symbol=settings.BENCHMARK_LISTED
            )
            benchmark_otc_df = prepare_benchmark(
                db, fetcher, symbol=settings.BENCHMARK_OTC
            )

            for code in stock_codes:
                # 先解析股票所屬市場以選擇正確的大盤基準
                from src.analyzer_core import resolve_stock_symbol
                stock_info_preview = resolve_stock_symbol(db, code)
                stock_market = stock_info_preview.get("market", "listed")
                benchmark_df = benchmark_otc_df if stock_market == "otc" else benchmark_listed_df

                result = analyze_stock(
                    db=db,
                    fetcher=fetcher,
                    stock_input=code,
                    benchmark_df=benchmark_df,
                    force_fetch=False,
                    settings=settings,
                )
                results.append((code, result))

        return results

    async def cmd_disposition(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """處理 /disposition 或 /disp 指令 → 查詢目前台股所有處置股票名單與出關倒數."""
        if not self._is_authorized(update):
            return

        from src.disposition import DispositionManager
        with DBManager(self.settings.DB_PATH) as db:
            disp_mgr = DispositionManager(db)
            active = disp_mgr.get_active_dispositions()

        if not active:
            await self._send_reply(update, "🟢 目前全市場查無處置中股票，或資料庫尚未同步。")
            return

        lines = [
            f"🚨 *全台股處置股票追蹤名單* (共 {len(active)} 檔)",
            "━━━━━━━━━━━━━━━━━━",
        ]

        exiting_soon = []
        normal_disp = []

        for sid, info in active.items():
            if info.get("is_exiting_soon"):
                exiting_soon.append(info)
            else:
                normal_disp.append(info)

        if exiting_soon:
            lines.append("🚀 *【即將出關 (剩餘 <= 2 天)】*")
            for item in exiting_soon:
                code = item["stock_id"]
                name = item["stock_name"]
                rem = item["remaining_trading_days"]
                end = item["end_date"]
                interval = item["matching_interval"]
                lines.append(f"• `{code} {name}` | `{interval}` | 剩餘 *{rem}* 天 (至 {end})")
            lines.append("")

        lines.append("⏳ *【處置管制中】*")
        for item in normal_disp:
            code = item["stock_id"]
            name = item["stock_name"]
            rem = item["remaining_trading_days"]
            end = item["end_date"]
            interval = item["matching_interval"]
            lines.append(f"• `{code} {name}` | {interval} | 剩餘 {rem} 天 (至 {end})")

        lines.append("")
        lines.append("💡 *提示*: 處置股被限制現股當沖且需預收款券，成交量驟降為法規所致。可使用 `/analyze <代號>` 檢視個股是否在處置期間維持高姿態抗跌橫盤！")

        await self._send_reply(update, "\n".join(lines), parse_mode="Markdown")

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """處理所有純文字與頻道貼文訊息 (自動路由指令或解析股票代號)."""
        if not self._is_authorized(update):
            return

        msg = update.effective_message
        if msg is None:
            return

        text = (msg.text or "").strip()
        if not text or len(text) > 100:
            return

        # 1. 若以 / 開頭，手動路由指令（保證在 Channel Posts 中 100% 生效）
        if text.startswith("/"):
            parts = text.split()
            cmd = parts[0].lower().lstrip("/").split("@")[0]
            context.args = parts[1:]

            if cmd in ("help", "start"):
                await self.cmd_help(update, context)
                return
            elif cmd == "status":
                await self.cmd_status(update, context)
                return
            elif cmd == "scan":
                await self.cmd_scan(update, context)
                return
            elif cmd in ("breadth", "market"):
                await self.cmd_breadth(update, context)
                return
            elif cmd in ("disp", "disposition"):
                await self.cmd_disposition(update, context)
                return
            elif cmd == "analyze":
                await self.cmd_analyze(update, context)
                return

        # 2. 嘗試解析為純股票代號 (純數字 4~6 位)
        tokens = text.replace(",", " ").split()
        stock_codes = [t for t in tokens if t.isdigit() and 4 <= len(t) <= 6]

        if stock_codes:
            context.args = stock_codes
            await self.cmd_analyze(update, context)
        else:
            await self._send_reply(
                update,
                "❓ 無法辨識指令\n\n"
                "• 輸入 `/help` 查看可用指令\n"
                "• 輸入 `/breadth` 查看全市場寬度折線圖\n"
                "• 或直接輸入股票代號 (如 `2330`) 進行分析",
                parse_mode="Markdown",
            )

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """全域例外處理器，避免未捕獲錯誤造成崩潰."""
        logger.error("Telegram Bot 處理更新時發生未捕獲的例外: %s", context.error, exc_info=context.error)

    def run(self) -> None:
        """啟動 Long Polling 監聽迴圈 (在獨立線程中呼叫)."""
        if not self.token:
            logger.error("TELEGRAM_BOT_TOKEN 未設定，無法啟動 Bot 指令監聽")
            return

        self._is_running = True
        logger.info("Telegram Bot 指令監聽啟動中...")

        # 建立 Application
        app = Application.builder().token(self.token).build()

        # 註冊指令處理器 (filters.ALL 支援普通訊息、頻道貼文與私訊)
        app.add_handler(CommandHandler("start", self.cmd_start, filters=filters.ALL))
        app.add_handler(CommandHandler("help", self.cmd_help, filters=filters.ALL))
        app.add_handler(CommandHandler("status", self.cmd_status, filters=filters.ALL))
        app.add_handler(CommandHandler("scan", self.cmd_scan, filters=filters.ALL))
        app.add_handler(CommandHandler("breadth", self.cmd_breadth, filters=filters.ALL))
        app.add_handler(CommandHandler("market", self.cmd_breadth, filters=filters.ALL))
        app.add_handler(CommandHandler("disp", self.cmd_disposition, filters=filters.ALL))
        app.add_handler(CommandHandler("disposition", self.cmd_disposition, filters=filters.ALL))
        app.add_handler(CommandHandler("analyze", self.cmd_analyze, filters=filters.ALL))

        # 處理純文字訊息與頻道貼文 (直接輸入股票代號或頻道轉發指令)
        app.add_handler(MessageHandler(filters.TEXT, self._handle_text))

        # 註冊全域錯誤處理器
        app.add_error_handler(self._error_handler)

        logger.info("Telegram Bot 已就緒，開始監聽指令 (支援群組/頻道/私聊)...")

        # 啟動長輪詢
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )

    def run_in_thread(self) -> threading.Thread:
        """在獨立的背景線程中啟動 Bot Long Polling.

        Returns:
            threading.Thread: 啟動後的背景線程
        """
        thread = threading.Thread(target=self.run, name="TelegramBotThread", daemon=True)
        thread.start()
        logger.info("Telegram Bot 指令監聽已在背景線程啟動")
        return thread
