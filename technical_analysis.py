# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

def _calculate_ema(data, period):
    return pd.Series(data).ewm(span=period, adjust=False).mean().to_numpy()

def _calculate_atr(high, low, close, period):
    # Рассчитываем True Range (TR)
    high_low = pd.Series(high - low)
    high_prev_close = np.abs(pd.Series(high) - pd.Series(close).shift(1))
    low_prev_close = np.abs(pd.Series(low) - pd.Series(close).shift(1))
    
    tr = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
    
    # Сглаживаем ATR с помощью Exponential Moving Average
    atr = tr.ewm(span=period, adjust=False).mean().to_numpy()
    return atr

def calculate_rsi(closes, period=14):
    if len(closes) < period: return np.array([]) # Защита от недостатка данных
    delta = pd.Series(closes).diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.to_numpy()

def calculate_macd(closes, fast_period=12, slow_period=26, signal_period=9):
    ema_fast = _calculate_ema(closes, fast_period)
    ema_slow = _calculate_ema(closes, slow_period)
    macd_line = ema_fast - ema_slow
    signal_line = _calculate_ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def get_trend_and_volatility(bars_df, settings):
    """Безопасная функция для определения тренда и волатильности по DataFrame."""
    ema_slow_period = settings.get('ema_slow', 26)
    if len(bars_df) < ema_slow_period:
        return "Н/Д", "Н/Д", None, None, None, None

    try:
        closes = bars_df['close'].to_numpy()
        highs = bars_df['high'].to_numpy()
        lows = bars_df['low'].to_numpy()
    except KeyError:
        return "Ошибка данных", "Ошибка данных", None, None, None, None

    # Тренд
    ema_fast = _calculate_ema(closes, settings.get('ema_fast', 9))
    ema_slow = _calculate_ema(closes, ema_slow_period)
    trend = "Восходящий" if ema_fast[-1] > ema_slow[-1] else "Нисходящий" if ema_fast[-1] < ema_slow[-1] else "Боковой"

    # Волатильность
    atr = _calculate_atr(highs, lows, closes, settings.get('atr_period', 14))
    avg_price = closes.mean()
    volatility = "Н/Д"
    if avg_price > 0 and atr.any():
        volatility_percent = (atr[-1] / avg_price) * 100
        if volatility_percent < 0.1:
            volatility = "Низкая"
        elif volatility_percent > 0.5:
            volatility = "Высокая"
        else:
            volatility = "Нормальная"
    
    # RSI и MACD
    rsi = calculate_rsi(closes, period=settings.get('rsi_period', 14))
    macd_line, signal_line, _ = calculate_macd(closes, settings.get('macd_fast', 12), settings.get('macd_slow', 26), settings.get('macd_signal', 9))

    return trend, volatility, atr, rsi[-1] if rsi.any() else None, macd_line[-1], signal_line[-1]

def calculate_order(signal, bars_df, atr, profile_levels, atr_multiplier, risk_reward_ratio):
    """Рассчитывает параметры ордера, используя ATR, уровни профиля и свинг-уровни."""
    poc, vah, val = profile_levels
    last_bar = bars_df.iloc[-1]
    entry_price = last_bar['close']
    
    lookback_period = 10 # Период для поиска свинга
    swing_high = bars_df['high'].tail(lookback_period).max()
    swing_low = bars_df['low'].tail(lookback_period).min()
    buffer = atr * 0.2 # Небольшой буфер

    if signal == 'LONG':
        # SL либо за свинг-лоу, либо по ATR, в зависимости от того, что дальше
        stop_loss_swing = swing_low - buffer
        stop_loss_atr = entry_price - atr * atr_multiplier
        stop_loss = min(stop_loss_swing, stop_loss_atr)

        # Цель: ближайший уровень сопротивления (VAH или POC), но не меньше, чем R:R
        potential_tp = [p for p in [vah, poc] if p and p > entry_price]
        atr_tp = entry_price + (entry_price - stop_loss) * risk_reward_ratio
        
        if potential_tp:
            best_tp = min(potential_tp)
            take_profit = max(best_tp, atr_tp)
        else:
            take_profit = atr_tp

    elif signal == 'SHORT':
        # SL либо за свинг-хай, либо по ATR, в зависимости от того, что дальше
        stop_loss_swing = swing_high + buffer
        stop_loss_atr = entry_price + atr * atr_multiplier
        stop_loss = max(stop_loss_swing, stop_loss_atr)

        # Цель: ближайший уровень поддержки (VAL или POC), но не меньше, чем R:R
        potential_tp = [p for p in [val, poc] if p and p < entry_price]
        atr_tp = entry_price - (stop_loss - entry_price) * risk_reward_ratio

        if potential_tp:
            best_tp = max(potential_tp)
            take_profit = min(best_tp, atr_tp)
        else:
            take_profit = atr_tp
    else:
        return None

    return entry_price, stop_loss, take_profit
