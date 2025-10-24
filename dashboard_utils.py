# -*- coding: utf-8 -*-
import numpy as np

def render_text_chart(data: list, width: int = 50, height: int = 8) -> str:
    """Рендерит простой текстовый график на основе списка числовых данных."""
    if not data:
        return "Нет данных для графика"

    data = np.array(data[-width:])
    min_val, max_val = np.min(data), np.max(data)

    if max_val == min_val:
        return "Данные не меняются"

    # Нормализуем данные в диапазон высоты графика
    scaled_data = (data - min_val) / (max_val - min_val) * (height - 1)
    scaled_data = np.round(scaled_data).astype(int)

    chart = [[' ' for _ in range(width)] for _ in range(height)]

    for x, y in enumerate(scaled_data):
        if x < width:
            chart[height - 1 - y][x] = '█'
    
    # Преобразуем в строки
    chart_str = "\n".join([''.join(row) for row in chart])
    return f"{chart_str}\nMin: {min_val:.2f} | Max: {max_val:.2f}"
