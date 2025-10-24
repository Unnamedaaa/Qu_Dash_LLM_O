# -*- coding: utf-8 -*-

# -- Настройки подключения к QUIK --
# Если QUIK на том же компьютере, оставляем localhost
QUIK_HOST = 'localhost'

# -- Настройки AI-модели --
OLLAMA_HOST = 'http://localhost'
OLLAMA_PORT = 11434
# Укажите точное имя модели, которую вы скачали в Ollama
MODEL_NAME = 'gemma3:4b'

# -- Настройки анализа --
# Таймфреймы для анализа (M = минуты)
TIMEFRAMES = ["1M", "15M", "240M"] 

# Инструмент для анализа
CLASS_CODE = "SPBFUT"
SEC_CODE = "SiZ5"

# -- Настройки торговой логики --
# Если True, бот будет отправлять реальные заявки. ВНИМАНИЕ: ИСПОЛЬЗОВАТЬ С ОСТОРОЖНОСТЬЮ!
REAL_TRADING_ENABLED = False

# Процент от депозита для одной сделки (для расчета объема позиции)
RISK_PER_TRADE_PERCENT = 1.0

# Множитель ATR для расчета Stop Loss
ATR_SL_MULTIPLIER = 2.0

# Соотношение Риск/Прибыль для расчета Take Profit
RISK_REWARD_RATIO = 1.5

# -- Детальные настройки логики и индикаторов --
INDICATOR_SETTINGS = {
    'ema_fast': 9,
    'ema_slow': 20,
    'atr_period': 14,
    'rsi_period': 14,
    'macd_fast': 12,
    'macd_slow': 26,
    'macd_signal': 9,
    'vp_period': 50, # Период для расчета профиля рынка
    'vp_value_area_percent': 70 # Процент для зоны стоимости
}

BOT_LOGIC_SETTINGS = {
    'ai_context_bar_count': 10, # Кол-во минутных свечей для контекста AI
    'post_trade_cooldown_sec': 30 # Пауза в секундах после сделки
}

UI_SETTINGS = {
    'chart_bar_count': 50 # Кол-во свечей на графике в дашборде
}

# -- Настройки модулей анализа --
ANALYSIS_CONFIG = {
    'cvd': {
        'period': 200,
        'divergence_lookback': 60
    },
    'clusters': {
        'time_window_sec': 15,
        'min_total_volume': 20
    },
    'icebergs': {
        'min_size_to_track': 500,
        'trigger_volume_ratio': 0.5,
        'time_window_sec': 60
    }
}

# -- Настройки бота --
# Таймфрейм, по которому бот принимает решение о запуске анализа
PRIMARY_TIMEFRAME = "1M"
