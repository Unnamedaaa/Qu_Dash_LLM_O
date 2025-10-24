# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

def calculate_volume_profile(bars_df: pd.DataFrame, settings: dict):
    """Рассчитывает Профиль Рынка (POC, VAH, VAL) для заданного DataFrame."""
    period = settings.get('vp_period', 50)
    value_area_percent = settings.get('vp_value_area_percent', 70.0)

    if len(bars_df) < period:
        return None, None, None

    profile_bars = bars_df.tail(period)
    
    min_price = profile_bars['low'].min()
    max_price = profile_bars['high'].max()
    
    # Определение шага цены. Если не можем определить, выходим.
    # Пробуем угадать шаг цены по последним данным
    price_diffs = profile_bars['close'].diff().abs().dropna()
    min_step = price_diffs[price_diffs > 0].min()
    if pd.isna(min_step) or min_step == 0:
        min_step = 1 # Запасной вариант, если шаг цены определить не удалось

    price_step = min_step

    num_levels = int((max_price - min_price) / price_step) + 1
    if num_levels > 2000: # Ограничение, чтобы избежать слишком больших вычислений
        return None, None, None

    price_levels = min_price + np.arange(num_levels) * price_step
    volume_at_price = np.zeros(num_levels)

    for _, row in profile_bars.iterrows():
        vol = row['volume']
        high = row['high']
        low = row['low']
        
        # Распределяем объем по уровням, затронутым свечой
        start_idx = int(max(0, (low - min_price) / price_step))
        end_idx = int(min(num_levels - 1, (high - min_price) / price_step))
        
        levels_in_bar = (end_idx - start_idx) + 1
        vol_per_level = vol / levels_in_bar if levels_in_bar > 0 else vol
        
        for i in range(start_idx, end_idx + 1):
            volume_at_price[i] += vol_per_level

    if np.sum(volume_at_price) == 0:
        return None, None, None

    # Находим POC
    poc_index = np.argmax(volume_at_price)
    poc = price_levels[poc_index]

    # Расчет Value Area (VA)
    total_volume = np.sum(volume_at_price)
    target_volume = total_volume * (value_area_percent / 100.0)

    # Итеративно расширяем зону стоимости от POC
    current_volume = volume_at_price[poc_index]
    vah_idx, val_idx = poc_index, poc_index

    while current_volume < target_volume:
        vah_idx += 1
        val_idx -= 1

        vol_at_vah = volume_at_price[vah_idx] if vah_idx < num_levels else 0
        vol_at_val = volume_at_price[val_idx] if val_idx >= 0 else 0

        if vol_at_vah >= vol_at_val:
            current_volume += vol_at_vah
            if vol_at_vah == 0 and vol_at_val == 0: break # Выход, если расширяться некуда
        else:
            current_volume += vol_at_val
    
    vah = price_levels[min(vah_idx, num_levels - 1)]
    val = price_levels[max(val_idx, 0)]

    return poc, vah, val
