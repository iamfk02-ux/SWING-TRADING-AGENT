"""
Mathematical indicator computations without external TA-Lib dependencies.
Self-contained, fast, and robust calculations for:
- Exponential Moving Average (EMA)
- Simple Moving Average (SMA)
- Average True Range (ATR)
- Average Directional Index (ADX) & directional movements (+DI, -DI)
- Bollinger Bands (Upper, Lower, Middle, BandWidth, BandWidth Percentile)
- Relative Strength Index (RSI)
- Donchian Channels (High, Low, Mid)
"""

import numpy as np
import pandas as pd


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def compute_sma(series: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average."""
    return series.rolling(window=period).mean()


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Average True Range using Wilder's smoothing.
    Expects df to have ['high', 'low', 'close'].
    """
    high = df['high']
    low = df['low']
    prev_close = df['close'].shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder's smoothing (equivalent to ewm with alpha=1/period)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    return atr


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Calculate ADX, +DI, -DI.
    Returns DataFrame with columns ['plus_di', 'minus_di', 'adx'].
    """
    high = df['high']
    low = df['low']
    close = df['close']

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm_series = pd.Series(plus_dm, index=df.index)
    minus_dm_series = pd.Series(minus_dm, index=df.index)

    atr = compute_atr(df, period)
    # Avoid zero division
    safe_atr = atr.replace(0, np.nan)

    smoothed_plus_dm = plus_dm_series.ewm(alpha=1.0 / period, adjust=False).mean()
    smoothed_minus_dm = minus_dm_series.ewm(alpha=1.0 / period, adjust=False).mean()

    plus_di = 100.0 * (smoothed_plus_dm / safe_atr)
    minus_di = 100.0 * (smoothed_minus_dm / safe_atr)

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * ((plus_di - minus_di).abs() / di_sum)
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()

    return pd.DataFrame({
        'plus_di': plus_di.fillna(0.0),
        'minus_di': minus_di.fillna(0.0),
        'adx': adx.fillna(0.0)
    }, index=df.index)


def compute_bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0, lookback_pct: int = 126) -> pd.DataFrame:
    """
    Calculate Bollinger Bands and BandWidth Percentile (compression indicator).
    Returns DataFrame with ['bb_mid', 'bb_upper', 'bb_lower', 'bb_width', 'bb_width_pct'].
    """
    mid = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = mid + (num_std * std)
    lower = mid - (num_std * std)
    
    # Normalized width: (upper - lower) / mid
    safe_mid = mid.replace(0, np.nan)
    width = (upper - lower) / safe_mid

    # Rolling percentile rank over historical lookback (e.g. 126 days ~ 6 months)
    # Value ranges from 0.0 (narrowest) to 1.0 (widest)
    def pct_rank(window):
        val = window[-1]
        return (window < val).mean()

    width_pct = width.rolling(window=lookback_pct).apply(pct_rank, raw=True)

    return pd.DataFrame({
        'bb_mid': mid,
        'bb_upper': upper,
        'bb_lower': lower,
        'bb_width': width,
        'bb_width_pct': width_pct
    }, index=series.index)


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (RSI)."""
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    gain_series = pd.Series(gain, index=series.index)
    loss_series = pd.Series(loss, index=series.index)

    avg_gain = gain_series.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss_series.ewm(alpha=1.0 / period, adjust=False).mean()

    safe_loss = avg_loss.replace(0, np.nan)
    rs = avg_gain / safe_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))

    # Where avg_loss was 0, RSI is 100; where avg_gain was 0, RSI is 0
    rsi = rsi.where(avg_loss != 0, 100.0)
    rsi = rsi.where(avg_gain != 0, 0.0)
    return rsi


def compute_donchian(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Calculate Donchian Channels (excluding current bar to avoid lookahead bias).
    Returns DataFrame with ['donchian_high', 'donchian_low', 'donchian_mid'].
    """
    # Shift by 1 so current bar cannot see its own high/low
    high_shifted = df['high'].shift(1)
    low_shifted = df['low'].shift(1)

    donchian_high = high_shifted.rolling(window=period).max()
    donchian_low = low_shifted.rolling(window=period).min()
    donchian_mid = (donchian_high + donchian_low) / 2.0

    return pd.DataFrame({
        'donchian_high': donchian_high,
        'donchian_low': donchian_low,
        'donchian_mid': donchian_mid
    }, index=df.index)
