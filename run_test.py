"""
Multi-Strategy Test Runner:
Tests all 3 strategies (Trend, Breakout, Mean-Reversion) simultaneously across portfolio symbols
and prints a complete performance audit.
"""

from pathlib import Path
import yaml
import sys

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from agents.data_agent import DataAgent
from backtest.engine import BacktestEngine
from core.database import init_db


def main():
    print("=" * 70)
    print("    MULTI-AGENT SWING TRADING SYSTEM - 3-STRATEGY AUDIT")
    print("    (Trend Following + Donchian Breakout + Mean Reversion)")
    print("=" * 70)

    config_path = ROOT_DIR / "config" / "settings.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Allow custom bar count and timeframe override from CLI / Dashboard
    timeframe = config.get("data", {}).get("timeframe", "H1").upper()
    if len(sys.argv) > 1:
        try:
            custom_bars = int(sys.argv[1])
            if len(sys.argv) > 2:
                timeframe = sys.argv[2].upper()
            config.setdefault("data", {})["timeframe"] = timeframe
            if timeframe == "H1":
                config["data"]["h1_bars"] = custom_bars
            else:
                config["data"]["h4_bars"] = custom_bars
            config["data"]["d1_bars"] = max(200, custom_bars // (24 if timeframe == "H1" else 6))
            print(f"[*] Custom Lookback Applied: {custom_bars} {timeframe} bars")
        except ValueError:
            pass

    init_db()

    data_agent = DataAgent(config)
    engine = BacktestEngine(config)

    forex_syms = config.get("symbols", {}).get("forex", ["EURUSD", "GBPUSD"])
    metal_syms = config.get("symbols", {}).get("metals", ["XAUUSD"])
    symbols = forex_syms + metal_syms
    print(f"\nSimulating 3-strategy portfolio across {len(symbols)} symbols ({timeframe} candles)...")
    print("-" * 70)
    print(f"{'SYMBOL':<10} | {'TRADES':<7} | {'WIN RATE':<9} | {'EXP (R)':<8} | {'PF':<6} | {'MAX DD':<7} | {'PROFIT'}")
    print("-" * 70)

    all_results = []
    for sym in symbols:
        df_d1, df_lower = data_agent.get_bars(sym, timeframe=timeframe)
        res = engine.run(sym, df_d1, df_lower)
        m = res["metrics"]
        all_results.append(m)
        print(f"{sym:<10} | {m['total_trades']:<7} | {m['win_rate_pct']:<8}% | {m['expectancy_r']:<8} | {m['profit_factor']:<6} | {m['max_drawdown_pct']:<6}% | ${m['net_profit_usd']}")

    print("-" * 70)
    total_trades = sum(r["total_trades"] for r in all_results)
    profitable = [r for r in all_results if r["total_trades"] > 0]
    avg_pf = round(sum(r["profit_factor"] for r in profitable) / max(1, len(profitable)), 2)
    max_dd = max(r["max_drawdown_pct"] for r in all_results) if all_results else 0.0
    total_profit = round(sum(r["net_profit_usd"] for r in all_results), 2)

    print(f"COMBINED TOTAL TRADES: {total_trades}")
    print(f"PORTFOLIO PROFIT FACTOR: {avg_pf}")
    print(f"MAX OBSERVED DRAWDOWN  : {max_dd}% (Limit: 20%)")
    print(f"NET AUDITED PROFIT     : ${total_profit}")
    print("=" * 70)
    print("\n[SUCCESS] Multi-Strategy verification completed!")


if __name__ == "__main__":
    main()
