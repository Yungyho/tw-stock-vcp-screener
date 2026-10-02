"""RS Rating 相對強度評級模組 (Relative Strength Rating).

採用 IBD / Minervini 式加權報酬率，計算個股在全市場的百分位 (1~99)。
公式: 2 x (近3月報酬) + (近6月) + (近9月) + (近12月)
"""

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _weighted_return(closes: pd.Series) -> Optional[float]:
    """計算加權報酬率原始分數."""
    if len(closes) < 252:
        return None
    c_now = float(closes.iloc[-1])
    if c_now <= 0:
        return None

    def _ret(days: int) -> float:
        base = float(closes.iloc[-days])
        return (c_now / base - 1.0) if base > 0 else 0.0

    return 2.0 * _ret(63) + _ret(126) + _ret(189) + _ret(252)


def build_rs_ratings(db, stocks: List[Dict[str, Any]]) -> Dict[str, int]:
    """計算全市場 RS Rating.

    Returns:
        Dict[str, int]: {stock_id: rs_rating}，範圍 1~99
    """
    raw: Dict[str, float] = {}
    for s in stocks:
        sid = s.get("stock_id")
        if not sid:
            continue
        df = db.get_price_history(sid, days=350)
        if df.empty or len(df) < 252:
            continue
        score = _weighted_return(df["close"])
        if score is not None:
            raw[sid] = score

    if not raw:
        logger.warning("RS Rating 計算失敗: 無足夠樣本")
        return {}

    series = pd.Series(raw)
    pct = series.rank(pct=True) * 98.0 + 1.0
    ratings = {k: int(round(v)) for k, v in pct.items()}
    logger.info("RS Rating 計算完成，樣本 %d 檔", len(ratings))
    return ratings
