import logging
import os
import requests
from typing import List, Dict, Optional
from src.notifier.base import BaseNotifier

class TelegramNotifier(BaseNotifier):
    """
    Telegram Bot 通知器實作
    """
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send_text(self, message: str) -> bool:
        """
        發送純文字訊息
        若訊息超過 4096 字元，會自動分段發送。
        若 Markdown 解析失敗，會自動降級為純文字發送。
        """
        if not self.token or not self.chat_id:
            logging.warning("尚未設定 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，略過推播發送")
            return False

        max_length = 4096
        messages_to_send = [message[i:i+max_length] for i in range(0, len(message), max_length)]
        
        success = True
        for msg in messages_to_send:
            try:
                payload = {
                    "chat_id": self.chat_id,
                    "text": msg,
                    "parse_mode": "Markdown"
                }
                response = requests.post(self.api_url, json=payload, timeout=10)
                response.raise_for_status()
                logging.info("Telegram 訊息發送成功")
            except requests.exceptions.RequestException as e:
                # 若 Markdown 格式錯誤，嘗試以無 Markdown 格式再次發送備援
                try:
                    payload = {"chat_id": self.chat_id, "text": msg}
                    fallback_resp = requests.post(self.api_url, json=payload, timeout=10)
                    fallback_resp.raise_for_status()
                    logging.info("Telegram 訊息 (純文字降級模式) 發送成功")
                except Exception as fallback_err:
                    err_msg = str(e)
                    if hasattr(e, "response") and e.response is not None:
                        try:
                            err_msg = e.response.json().get("description", e.response.text)
                        except Exception:
                            err_msg = e.response.text
                    logging.error(f"發送 Telegram 訊息失敗: {err_msg} (Chat ID: {self.chat_id})")
                    success = False
        
        return success

    def send_scan_report(self, results: List[Dict], scan_date: str) -> bool:
        """
        發送掃描報告，最多顯示前 10 筆結果
        """
        if not results:
            message = f"📊 *VCP Stage 2 掃描報告*\n📅 {scan_date}\n\n❌ 今日無符合條件的個股"
            return self.send_text(message)
            
        count = len(results)
        display_results = results[:10]
        
        message = f"📊 *VCP Stage 2 掃描報告*\n📅 {scan_date}（共 {count} 檔符合）\n\n🏆 *Top Results:*\n\n"
        
        number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for idx, res in enumerate(display_results):
            emoji = number_emojis[idx] if idx < 10 else f"{idx+1}."
            stock_id = res.get("stock_id", "N/A")
            name = res.get("name", "N/A")
            score = res.get("score", 0)
            close = res.get("close", 0)
            distance = round(res.get("distance_to_pivot", 0), 1)
            contractions = res.get("contractions", [])
            contraction_str = "→".join(f"{c}%" for c in contractions) if contractions else "N/A"
            volume_change = round(res.get("volume_change", 0), 1)
            tt_pass = res.get("trend_template_pass", 0)
            pivot = res.get("pivot_price", 0)
            mcap = res.get("market_cap", 0.0)
            turnover = res.get("turnover_twd", 0.0)
            beta = res.get("beta_1y")

            # 市值格式化
            mcap_str = f"{mcap / 1e8:.1f}億" if mcap >= 1e8 else (f"{mcap / 1e4:.0f}萬" if mcap > 0 else "N/A")
            # 成交金額格式化
            turnover_str = f"{turnover / 1e8:.2f}億" if turnover >= 1e8 else f"{turnover / 1e4:.0f}萬"
            # Beta 格式化
            beta_str = f"{beta:.2f}" if beta is not None else "N/A"
            rs_val = res.get("rs_rating")
            rs_str = str(rs_val) if rs_val else "N/A"

            # Yahoo 奇摩股市技術分析圖連結
            yahoo_tech_url = f"https://tw.stock.yahoo.com/quote/{stock_id}/technical-analysis"

            # 階段型態標籤
            stage_tag = res.get("action_stage", "")
            stage_badge = ""
            if stage_tag == "RECENT_BREAKOUT":
                stage_badge = " [🚀剛突破1~3日]"
            elif stage_tag == "PIVOT_RETEST":
                stage_badge = " [🔄回踩樞紐]"
            elif stage_tag == "BUY_READY":
                stage_badge = " [🎯買點警戒]"

            # 處置與注意標籤
            disp_info = res.get("disposition_info")
            attn_info = res.get("attention_info")
            disp_badge = ""
            if disp_info:
                interval = disp_info.get("matching_interval", "處置")
                rem = disp_info.get("remaining_trading_days", 0)
                soon = "🚀即將出關" if disp_info.get("is_exiting_soon") else f"剩{rem}天"
                disp_badge = f" [🚨{interval}/{soon}]"
            elif attn_info:
                disp_badge = " [⚠️注意股]"

            message += f"{emoji} *[{stock_id} {name}]({yahoo_tech_url})*{stage_badge}{disp_badge} ⭐ {score:.0f}分\n"
            message += f"   💰 收盤: {close:,.0f} | 🎯 突破: {pivot:,.0f} | 距突破: {distance}%\n"
            message += f"   🏢 市值: {mcap_str} | 💵 均金額: {turnover_str}\n"
            message += f"   ⚡ Beta: {beta_str} | 🔥 RS: {rs_str} | 📉 收斂: {contraction_str}\n"
            message += f"   📊 量縮: {volume_change}% | ✅ TT: {tt_pass}/9\n"
            message += f"   🔗 [開啟技術分析圖]({yahoo_tech_url})\n\n"
            
        return self.send_text(message)

    def send_photo(self, photo_path: str, caption: str = "") -> bool:
        """發送本地圖片檔案 (支援 Telegram Markdown 說明文字).

        Args:
            photo_path: 本地圖片檔案路徑 (例如 logs/market_breadth.png)
            caption: 隨附之圖表說明文字 (上限 1024 字元)

        Returns:
            bool: 是否發送成功
        """
        if not self.token or not self.chat_id:
            logging.warning("尚未設定 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，略過圖片發送")
            return False

        if not os.path.exists(photo_path):
            logging.warning("欲發送之圖片檔案不存在: %s", photo_path)
            return False

        photo_url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        try:
            with open(photo_path, "rb") as f:
                files = {"photo": f}
                data = {
                    "chat_id": self.chat_id,
                    "caption": caption[:1024],
                    "parse_mode": "Markdown",
                }
                response = requests.post(photo_url, data=data, files=files, timeout=25)
                response.raise_for_status()
                logging.info("Telegram 圖片發送成功: %s", photo_path)
                return True
        except requests.exceptions.RequestException as e:
            # 若 Markdown 格式錯誤，嘗試純文字發送備援
            try:
                with open(photo_path, "rb") as f:
                    files = {"photo": f}
                    data = {
                        "chat_id": self.chat_id,
                        "caption": caption[:1024],
                    }
                    fallback_resp = requests.post(photo_url, data=data, files=files, timeout=25)
                    fallback_resp.raise_for_status()
                    logging.info("Telegram 圖片 (純文字降級模式) 發送成功")
                    return True
            except Exception as fallback_err:
                err_msg = str(e)
                if hasattr(e, "response") and e.response is not None:
                    try:
                        err_msg = e.response.json().get("description", e.response.text)
                    except Exception:
                        err_msg = e.response.text
                logging.error(f"發送 Telegram 圖片失敗: {err_msg} (Chat ID: {self.chat_id})")
                return False
