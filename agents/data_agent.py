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
        self.timeframe = data_cfg.get("timeframe", "H1").upper()
        self.h1_bars = data_cfg.get("h1_bars", 2000)
        self.h4_bars = data_cfg.get("h4_bars", 1000)
        self.d1_bars = data_cfg.get("d1_bars", 400)
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def get_bars(self, symbol: str, timeframe: Optional[str] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Returns (df_d1, df_lower) for the given symbol.
        df_lower will be H1 candles if timeframe="H1" (default) or H4 candles if timeframe="H4".
        DataFrame columns: ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        """
        tf = (timeframe or self.timeframe).upper()

        if self.source == "mt5":
            try:
                import MetaTrader5 as mt5
                if not mt5.initialize():
                    return self._get_fallback_data(symbol, tf)

                # Ensure symbol is active in MarketWatch
                mt5.symbol_select(symbol, True)
                
                # Fetch D1
                rates_d1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, self.d1_bars)
                
                # Fetch lower timeframe (H1 or H4)
                mt5_tf = mt5.TIMEFRAME_H1 if tf == "H1" else mt5.TIMEFRAME_H4
                bar_count = self.h1_bars if tf == "H1" else self.h4_bars
                rates_lower = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, bar_count)
                
                if rates_d1 is None or rates_lower is None or len(rates_d1) == 0 or len(rates_lower) == 0:
                    return self._get_fallback_data(symbol, tf)

                df_d1 = self._rates_to_df(rates_d1)
                df_lower = self._rates_to_df(rates_lower)

                # Cache to CSV
                df_d1.to_csv(DATA_DIR / f"{symbol}_D1.csv", index=False)
                df_lower.to_csv(DATA_DIR / f"{symbol}_{tf}.csv", index=False)
                return df_d1, df_lower
            except Exception as e:
                print(f"[NOTE] MT5 fetch notice for {symbol} ({tf}): {e}. Falling back to CSV/Synthetic.")
                return self._get_fallback_data(symbol, tf)

        return self._get_fallback_data(symbol, tf)

    def _rates_to_df(self, rates) -> pd.DataFrame:
        df = pd.DataFrame(rates)
        df["timestamp"] = pd.to_datetime(df["time"], unit="s")
        df = df[["timestamp", "open", "high", "low", "close", "tick_volume"]]
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        return df

    def _get_fallback_data(self, symbol: str, tf: str = "H1") -> Tuple[pd.DataFrame, pd.DataFrame]:
        d1_csv = DATA_DIR / f"{symbol}_D1.csv"
        lower_csv = DATA_DIR / f"{symbol}_{tf}.csv"

        if d1_csv.exists() and lower_csv.exists():
            df_d1 = pd.read_csv(d1_csv)
            df_lower = pd.read_csv(lower_csv)
            df_d1["timestamp"] = pd.to_datetime(df_d1["timestamp"])
            df_lower["timestamp"] = pd.to_datetime(df_lower["timestamp"])
            return df_d1, df_lower

        return self._generate_synthetic_data(symbol, tf)

    def _generate_synthetic_data(self, symbol: str, tf: str = "H1") -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Generates realistic price candles for testing without requiring an active MT5 broker.
        """
        np.random.seed(abs(hash(symbol)) % (2**31))
        
        base_price = 1.1000 if "EUR" in symbol or "USD" in symbol else (150.0 if "JPY" in symbol else 2600.0 if "XAU" in symbol else 65000.0)
        daily_bars = 400
        bars_per_day = 24 if tf == "H1" else 6
        step_hours = 1 if tf == "H1" else 4
        lower_bars = daily_bars * bars_per_day

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

        # Generate corresponding lower timeframe bars (H1 or H4)
        lower_rows = []
        current_time = dates_d1[0]
        curr_price = base_price
        ret_vol = 0.0015 if tf == "H1" else 0.003
        for _ in range(lower_bars):
            ret = np.random.normal(0.00001, ret_vol)
            c = curr_price * np.exp(ret)
            vol = c * ret_vol
            o = curr_price
            h = max(o, c) + abs(np.random.normal(0, vol * 0.4))
            l = min(o, c) - abs(np.random.normal(0, vol * 0.4))
            lower_rows.append({"timestamp": current_time, "open": o, "high": h, "low": l, "close": c, "volume": 250})
            curr_price = c
            current_time += timedelta(hours=step_hours)
        df_lower = pd.DataFrame(lower_rows)

        return df_d1, df_lower
