"""
Data Agent:
Fetches, cleans, and caches historical and real-time bars.
Supports:
1. Live MT5 terminal connection
2. Local CSV fallback (from data/history)
3. Synthetic realistic data generator (for zero-setup testing and validation)
"""

from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "history"


class DataAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        data_cfg = config.get("data", {})
        self.source = data_cfg.get("source", "synthetic")
        self.h4_bars = data_cfg.get("h4_bars", 2000)
        self.d1_bars = data_cfg.get("d1_bars", 500)
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def get_bars(self, symbol: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Returns (df_d1, df_h4) for the given symbol.
        DataFrame columns: ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        """
        if self.source == "mt5":
            try:
                import MetaTrader5 as mt5
                if not mt5.initialize():
                    return self._get_fallback_data(symbol)
                
                # Fetch D1
                rates_d1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, self.d1_bars)
                rates_h4 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 0, self.h4_bars)
                
                if rates_d1 is None or rates_h4 is None or len(rates_d1) == 0:
                    return self._get_fallback_data(symbol)
                    return self._get_fallback_data(symbol)

                df_d1 = self._rates_to_df(rates_d1)
                df_h4 = self._rates_to_df(rates_h4)

                # Cache to CSV
                df_d1.to_csv(DATA_DIR / f"{symbol}_D1.csv", index=False)
                df_h4.to_csv(DATA_DIR / f"{symbol}_H4.csv", index=False)
                return df_d1, df_h4
            except Exception as e:
                print(f"[NOTE] MT5 fetch notice: {e}. Falling back to CSV/Synthetic.")
                return self._get_fallback_data(symbol)

        return self._get_fallback_data(symbol)

    def _rates_to_df(self, rates) -> pd.DataFrame:
        df = pd.DataFrame(rates)
        df["timestamp"] = pd.to_datetime(df["time"], unit="s")
        df = df[["timestamp", "open", "high", "low", "close", "tick_volume"]]
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        return df

    def _get_fallback_data(self, symbol: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
        d1_csv = DATA_DIR / f"{symbol}_D1.csv"
        h4_csv = DATA_DIR / f"{symbol}_H4.csv"

        if d1_csv.exists() and h4_csv.exists():
            df_d1 = pd.read_csv(d1_csv)
            df_h4 = pd.read_csv(h4_csv)
            df_d1["timestamp"] = pd.to_datetime(df_d1["timestamp"])
            df_h4["timestamp"] = pd.to_datetime(df_h4["timestamp"])
            return df_d1, df_h4

        return self._generate_synthetic_data(symbol)

    def _generate_synthetic_data(self, symbol: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Generates realistic Geometric Brownian Motion price candles
        with intraday swings for testing without requiring an active MT5 broker.
        """
        np.random.seed(abs(hash(symbol)) % (2**31))
        
        base_price = 1.1000 if "EUR" in symbol or "USD" in symbol else (150.0 if "JPY" in symbol else 2000.0)
        daily_bars = 600
        h4_bars = daily_bars * 6

        # Generate D1
        dates_d1 = [datetime(2023, 1, 1) + timedelta(days=i) for i in range(daily_bars)]
        returns_d1 = np.random.normal(0.0002, 0.008, daily_bars)
        price_d1 = base_price * np.exp(np.cumsum(returns_d1))

        d1_rows = []
        for i, dt in enumerate(dates_d1):
            c = price_d1[i]
            vol = c * 0.008
            o = c + np.random.normal(0, vol * 0.3)
            h = max(o, c) + abs(np.random.normal(0, vol * 0.5))
            l = min(o, c) - abs(np.random.normal(0, vol * 0.5))
            d1_rows.append({"timestamp": dt, "open": o, "high": h, "low": l, "close": c, "volume": 1000})
        df_d1 = pd.DataFrame(d1_rows)

        # Generate corresponding H4 bars
        h4_rows = []
        current_time = dates_d1[0]
        curr_price = base_price
        for _ in range(h4_bars):
            ret = np.random.normal(0.00003, 0.003)
            c = curr_price * np.exp(ret)
            vol = c * 0.003
            o = curr_price
            h = max(o, c) + abs(np.random.normal(0, vol * 0.4))
            l = min(o, c) - abs(np.random.normal(0, vol * 0.4))
            h4_rows.append({"timestamp": current_time, "open": o, "high": h, "low": l, "close": c, "volume": 250})
            curr_price = c
            current_time += timedelta(hours=4)
        df_h4 = pd.DataFrame(h4_rows)

        return df_d1, df_h4
