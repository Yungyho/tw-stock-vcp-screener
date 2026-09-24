"""
Scorer Module

Calculates a comprehensive score based on Trend Template, VCP characteristics, and volume patterns.
"""

import pandas as pd
from typing import List, Dict

def calculate_score(trend_result: dict, vcp_result: dict, df: pd.DataFrame, market_df: pd.DataFrame | None = None) -> float:
    """
    Calculate a comprehensive score (0-100) based on trend and VCP results.
    
    Args:
        trend_result: Output from check_trend_template().
        vcp_result: Output from detect_vcp().
        df: The stock's price DataFrame.
        market_df: Optional market index DataFrame for relative strength.
        
    Returns:
        float: Total score from 0 to 100.
    """
    score = 0.0
    
    # 1. Trend Template score (25%)
    trend_score = (trend_result.get('score', 0) / 9.0) * 25.0
    score += trend_score
    
    # 2. VCP quality (25%)
    vcp_score = 0.0
    if vcp_result.get('is_vcp', False):
        vcp_score += 15.0
    if vcp_result.get('num_contractions', 0) >= 3:
        vcp_score += 5.0
    if vcp_result.get('tightness', 100.0) < 8.0:
        vcp_score += 5.0
    score += vcp_score
    
    # 3. Volume decline (15%)
    vol_score = 0.0
    if len(df) >= 50:
        recent_20d_avg_volume = df['Volume'].iloc[-20:].mean()
        past_50d_avg_volume = df['Volume'].iloc[-50:].mean()
        
        if past_50d_avg_volume > 0:
            ratio = recent_20d_avg_volume / past_50d_avg_volume
            vol_score = max(0.0, (1.0 - ratio)) * 15.0
    score += vol_score
    
    # 4. Distance to pivot (20%)
    dist_score = 0.0
    pivot = float(vcp_result.get('pivot_price', 0.0))
    dist = float(vcp_result.get('distance_to_pivot', float('inf')))
    if pivot > 0 and -5.0 <= dist <= 20.0:
        if 0.0 <= dist <= 2.0:
            dist_score = 20.0
        elif 0.0 <= dist <= 5.0 or -2.0 <= dist < 0.0:
            dist_score = 15.0
        elif dist <= 10.0:
            dist_score = 10.0
        else:
            dist_score = 5.0
    score += dist_score
    
    # 5. Relative strength (15%)
    rs_score = 7.5 # neutral by default
    if market_df is not None and not df.empty and not market_df.empty:
        if len(df) >= 50 and len(market_df) >= 50:
            s_col = "Close" if "Close" in df.columns else ("close" if "close" in df.columns else df.columns[0])
            m_col = "Close" if "Close" in market_df.columns else ("close" if "close" in market_df.columns else market_df.columns[0])
            stock_50d_start = float(df[s_col].iloc[-50])
            stock_current = float(df[s_col].iloc[-1])
            market_50d_start = float(market_df[m_col].iloc[-50])
            market_current = float(market_df[m_col].iloc[-1])
            
            stock_return = (stock_current - stock_50d_start) / stock_50d_start
            market_return = (market_current - market_50d_start) / market_50d_start
            
            if stock_return > 0 and market_return > 0:
                rs = stock_return / market_return
                rs_score = min(rs * 7.5, 15.0)
    score += rs_score
    
    return min(100.0, max(0.0, score))


def rank_results(results: List[Dict]) -> List[Dict]:
    """
    Sort results by score descending and add a 'rank' field.
    
    Args:
        results: List of result dictionaries containing a 'score' key.
        
    Returns:
        List[Dict]: Sorted list of results with rank added.
    """
    sorted_results = sorted(results, key=lambda x: x.get('score', 0), reverse=True)
    
    for i, res in enumerate(sorted_results):
        res['rank'] = i + 1
        
    return sorted_results
