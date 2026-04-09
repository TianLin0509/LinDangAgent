import sys
import os
import json

# Add the project root to sys.path
sys.path.append(os.getcwd())

from data.tushare_client import resolve_stock
from data.stock_sentiment import fetch_stock_sentiment

def scout_hengrui():
    stock_name = "600276"
    ts_code, resolved_name, err = resolve_stock(stock_name)
    if err:
        print(f"Error resolving stock: {err}")
        # Manual fallback if resolve fails but we know it's 600276.SH
        ts_code = "600276.SH"
        resolved_name = "恒瑞医药"

    print(f"Scouting {resolved_name} ({ts_code})...")
    
    try:
        bundle = fetch_stock_sentiment(ts_code, resolved_name)
        
        print("\n--- SENTIMENT ANALYSIS ---")
        print(f"Short term sentiment: {bundle.short_term.sentiment_label}")
        print(f"Bull points: {bundle.short_term.bull_points}")
        print(f"Bear points: {bundle.short_term.bear_points}")
        print(f"One liner: {bundle.short_term.one_liner}")
        
        print(f"\nMid term sentiment: {bundle.mid_term.sentiment_label}")
        print(f"Bull points: {bundle.mid_term.bull_points}")
        print(f"Bear points: {bundle.mid_term.bear_points}")
        print(f"One liner: {bundle.mid_term.one_liner}")
    except Exception as e:
        print(f"Error fetching sentiment: {e}")

if __name__ == "__main__":
    scout_hengrui()
