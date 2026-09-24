import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# 嘗試載入 matplotlib (若未安裝則優雅降級)
HAS_MATPLOTLIB = False
try:
    import matplotlib
    import matplotlib.pyplot as plt

    matplotlib.use("Agg")
    plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    HAS_MATPLOTLIB = True
except ImportError:
    logger.warning("系統尚未安裝 matplotlib，圖表繪製功能將暫時停用 (可執行 pip install matplotlib 安裝)")


def plot_equity_curve(
    equity_df: pd.DataFrame,
    output_path: str = "logs/backtest_equity_curve.png",
    strategy_name: str = "VCP Stage 2 Strategy",
) -> Optional[str]:
    """繪製策略資產淨值曲線、大盤基準對照與水下回撤圖 (Plot Equity Curve & Drawdown).

    Args:
        equity_df: 包含 date, total_equity, benchmark_price 等欄位的 DataFrame
        output_path: 圖檔儲存路徑
        strategy_name: 策略名稱

    Returns:
        Optional[str]: 儲存圖檔之絕對路徑，若失敗則回傳 None
    """
    if not HAS_MATPLOTLIB:
        logger.warning("未安裝 matplotlib，略過圖表生成")
        return None

    if equity_df.empty or "total_equity" not in equity_df.columns:
        logger.warning("Equity DataFrame 為空，無法繪製資產淨值曲線圖")
        return None

    try:
        df = equity_df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        initial_equity = float(df["total_equity"].iloc[0])

        # 計算策略累計報酬與淨值走勢 (以 1.0 為基準)
        df["strategy_norm"] = df["total_equity"] / initial_equity

        # 計算大盤基準走勢 (若存在)
        has_benchmark = False
        if "benchmark_price" in df.columns and df["benchmark_price"].dropna().count() >= 2:
            valid_bench = df.dropna(subset=["benchmark_price"])
            bench_start = float(valid_bench["benchmark_price"].iloc[0])
            if bench_start > 0:
                df["benchmark_norm"] = df["benchmark_price"] / bench_start
                has_benchmark = True

        # 計算水下回撤 (Drawdown %)
        df["peak"] = df["total_equity"].cummax()
        df["drawdown"] = (df["total_equity"] - df["peak"]) / df["peak"] * 100.0

        # 建立雙子圖 (上方: 淨值曲線，下方: 水下回撤)
        fig, (ax1, ax2) = plt.subplots(
            2,
            1,
            figsize=(12, 8),
            gridspec_kw={"height_ratios": [3, 1]},
            sharex=True,
        )

        # ── 子圖 1: 資產淨值曲線 ──
        ax1.plot(
            df["date"],
            df["strategy_norm"],
            label=f"{strategy_name} (策略淨值)",
            color="#1f77b4",
            linewidth=2.0,
        )

        if has_benchmark:
            ax1.plot(
                df["date"],
                df["benchmark_norm"],
                label="TAIEX 加權指數 (^TWII 大盤)",
                color="#7f7f7f",
                linestyle="--",
                linewidth=1.5,
                alpha=0.85,
            )

        ax1.set_title(f"台股 VCP Stage 2 策略歷史回測績效曲線 ({df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d')})", fontsize=14, fontweight="bold", pad=12)
        ax1.set_ylabel("累積淨值倍數 (Normalized Equity)", fontsize=11)
        ax1.grid(True, linestyle=":", alpha=0.6)
        ax1.legend(loc="upper left", frameon=True, facecolor="white", edgecolor="none", fontsize=10)
        ax1.axhline(1.0, color="gray", linestyle=":", linewidth=0.8, alpha=0.7)

        # ── 子圖 2: 水下回撤 (Drawdown) ──
        ax2.plot(df["date"], df["drawdown"], color="#d62728", linewidth=1.2, label="Drawdown (%)")
        ax2.fill_between(df["date"], df["drawdown"], 0, color="#d62728", alpha=0.3)
        ax2.set_ylabel("回撤幅度 (%)", fontsize=11)
        ax2.set_xlabel("日期", fontsize=11)
        ax2.grid(True, linestyle=":", alpha=0.6)
        ax2.set_ylim([min(-25, float(df["drawdown"].min()) * 1.15), 2])

        plt.tight_layout()

        # 確保輸出目錄存在
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        fig.savefig(str(out_file), dpi=200, bbox_inches="tight")
        plt.close(fig)

        logger.info("資產淨值曲線圖已成功輸出至: %s", out_file.resolve())
        return str(out_file.resolve())
    except Exception as e:
        logger.error("繪製資產淨值曲線時發生錯誤: %s", e, exc_info=True)
        return None


def plot_market_breadth(
    breadth_df: pd.DataFrame,
    output_path: str = "logs/market_breadth.png",
) -> Optional[str]:
    """繪製全市場寬度指標折線圖 (50MA% 與 200MA% 比例走勢與大盤對照).

    Args:
        breadth_df: calculate_market_breadth 回傳的 DataFrame (包含 pct_above_50ma, pct_above_200ma, benchmark_close)
        output_path: 圖檔輸出路徑

    Returns:
        Optional[str]: 輸出圖檔之絕對路徑，若失敗回傳 None
    """
    if not HAS_MATPLOTLIB:
        logger.warning("未安裝 matplotlib，略過市場寬度圖表繪製")
        return None

    if breadth_df.empty or "pct_above_50ma" not in breadth_df.columns:
        logger.warning("Market Breadth DataFrame 為空，無法繪製折線圖")
        return None

    try:
        df = breadth_df.copy()
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        df = df.reset_index()
        if "date" not in df.columns:
            df = df.rename(columns={"index": "date"})

        has_bench = (
            "benchmark_close" in df.columns
            and df["benchmark_close"].dropna().count() >= 5
        )

        date_start_str = df["date"].iloc[0].strftime("%Y-%m-%d")
        date_end_str = df["date"].iloc[-1].strftime("%Y-%m-%d")
        latest_50 = float(df["pct_above_50ma"].iloc[-1])
        latest_200 = float(df["pct_above_200ma"].iloc[-1])

        if has_bench:
            fig, (ax1, ax2) = plt.subplots(
                2,
                1,
                figsize=(12, 8),
                gridspec_kw={"height_ratios": [2.2, 1.0]},
                sharex=True,
            )
        else:
            fig, ax1 = plt.subplots(figsize=(12, 6))
            ax2 = None

        # ── 子圖 1: 50MA% 與 200MA% 折線圖 ──
        ax1.plot(
            df["date"],
            df["pct_above_50ma"],
            label=f"高於 50MA 比例 (季線 / 現值 {latest_50:.1f}%)",
            color="#00a896",
            linewidth=2.2,
        )
        ax1.plot(
            df["date"],
            df["pct_above_200ma"],
            label=f"高於 200MA 比例 (年線 / 現值 {latest_200:.1f}%)",
            color="#f77f00",
            linewidth=2.2,
        )

        # 關鍵多空基準線
        ax1.axhline(50.0, color="#6c757d", linestyle="--", linewidth=1.2, alpha=0.8, label="50% 多空分水嶺")
        ax1.axhline(70.0, color="#d62828", linestyle=":", linewidth=1.0, alpha=0.7, label="70% 超買過熱警戒區")
        ax1.axhline(30.0, color="#2a9d8f", linestyle=":", linewidth=1.0, alpha=0.7, label="30% 恐慌超賣築底區")

        # 區間著色 (超買/超賣區域提示)
        ax1.axhspan(70.0, 100.0, color="#d62828", alpha=0.06)
        ax1.axhspan(0.0, 30.0, color="#2a9d8f", alpha=0.06)

        ax1.set_ylim(0, 100)
        ax1.set_ylabel("站上均線個股比例 (%)", fontsize=11, fontweight="bold")
        ax1.set_title(
            f"台股全市場寬度指標 — 股價高於 50MA & 200MA 比例走勢 ({date_start_str} ~ {date_end_str})",
            fontsize=13,
            fontweight="bold",
            pad=12,
        )
        ax1.grid(True, linestyle=":", alpha=0.6)
        ax1.legend(loc="upper left", frameon=True, facecolor="white", edgecolor="#ced4da", fontsize=9.5)

        # ── 子圖 2: TAIEX 加權指數走勢對照 ──
        if has_bench and ax2 is not None:
            ax2.plot(
                df["date"],
                df["benchmark_close"],
                color="#2b2d42",
                linewidth=1.6,
                label=f"TAIEX 加權指數 (最新: {df['benchmark_close'].iloc[-1]:,.0f})",
            )
            ax2.fill_between(df["date"], df["benchmark_close"], color="#8d99ae", alpha=0.2)
            ax2.set_ylabel("加權指數", fontsize=11, fontweight="bold")
            ax2.set_xlabel("日期", fontsize=11)
            ax2.grid(True, linestyle=":", alpha=0.6)
            ax2.legend(loc="upper left", frameon=True, facecolor="white", edgecolor="#ced4da", fontsize=9.5)

            # 格式化千分位
            import matplotlib.ticker as ticker
            ax2.yaxis.set_major_formatter(ticker.StrMethodFormatter("{x:,.0f}"))
        else:
            ax1.set_xlabel("日期", fontsize=11)

        plt.tight_layout()

        # 輸出圖檔
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out_file), dpi=200, bbox_inches="tight")
        plt.close(fig)

        logger.info("全市場寬度指標折線圖已成功輸出至: %s", out_file.resolve())
        return str(out_file.resolve())

    except Exception as e:
        logger.error("繪製市場寬度指標折線圖時發生錯誤: %s", e, exc_info=True)
        return None
