# -*- coding: utf-8 -*-
import os
import time
import json
import logging
import threading
import traceback
import re
from datetime import datetime, time as dt_time, timedelta
from collections import deque

import numpy as np
from QuikPy import QuikPy
from colorama import Fore, Back, Style, init

from .config import Config
from .data_models import BarData, IndicatorData
from .indicators import IndicatorCalculator
from .alerts import AlertSystem
from .dashboard import Dashboard
from .analysis import market_analyzer

class QuikMonitor:
    def __init__(self, config):
        self.config = config
        self.logger = self.setup_logger()
        self.logger.info("Инициализация QuikMonitor...")
        
        try:
            self.class_code = config.get('class_code')
            self.sec_code = config.get('sec_code')
            self.timeframes = config.get('timeframes', ['1M'])
            self.update_interval = config.get('update_interval', 3)
            self.running = True
            self.qp = QuikPy()
            self.alert_system = AlertSystem(config)
            self.dashboard = Dashboard(self)
            self.bid_volume, self.offer_volume = 0, 0
            self.bid_levels, self.offer_levels = 0, 0
            self.top_bids, self.top_offers = [], []
            self.all_bid_orders, self.all_offer_orders = [], []
            self.last_change_time = datetime.now()
            self.oscillator_value, self.last_oscillator = 0, 0
            self.tick_history = deque(maxlen=2000)
            self.tick_volume, self.tick_count = 0, 0
            self.buy_volume, self.sell_volume = 0, 0
            self.last_tick_time = datetime.now()
            self.price_direction = "→"
            self.bars = {tf: deque(maxlen=200) for tf in self.timeframes}
            self.current_bars = {tf: None for tf in self.timeframes}
            self.indicators = {tf: IndicatorData() for tf in self.timeframes}
            self.indicator_calculators = {tf: IndicatorCalculator(config) for tf in self.timeframes}
            self.data_lock = threading.RLock()
            self.redraw_needed = False
            self.cache_file = f'quik_cache_{self.sec_code}.json'
            self.quote_interval, self.trade_interval = 0.15, 0.08
            self.last_quote_time, self.last_trade_time = time.time(), time.time()
            self._init_analysis_modules(config)
            self.logger.info("Инициализация завершена успешно")
        except Exception as e:
            self.logger.error(f"Ошибка инициализации: {str(e)}")
            self.logger.error(traceback.format_exc())
            raise

    def _init_analysis_modules(self, config):
        self.tape_analysis_enabled = config.get('tape_analysis.enabled', False)
        if self.tape_analysis_enabled:
            self.large_lot_threshold = config.get('tape_analysis.large_lot_threshold', 100)
            self.tape_speed_interval = timedelta(seconds=config.get('tape_analysis.tape_speed_interval_sec', 5))
            self.tape_speed_alert_tps = config.get('tape_analysis.tape_speed_alert_threshold_tps', 10)
            self.recent_trades, self.large_trades = deque(), deque(maxlen=10)
            self.tape_speed = 0

        self.cvd_analysis_enabled = config.get('cvd_analysis.enabled', False)
        if self.cvd_analysis_enabled:
            self.cvd_period = config.get('cvd_analysis.period', 200)
            self.divergence_lookback = config.get('cvd_analysis.divergence_lookback', 60)
            self.cvd_history = deque(maxlen=self.cvd_period)
            self.price_history_for_cvd = deque(maxlen=self.cvd_period)
            self.cumulative_delta, self.session_max_cvd, self.session_min_cvd, self.active_divergence = 0, None, None, None

        self.atr_filter_enabled = config.get('atr_filter.enabled', False)
        if self.atr_filter_enabled:
            self.atr_filter_tf = config.get('atr_filter.main_tf', '15M')
            self.min_atr_percent, self.max_atr_percent = config.get('atr_filter.min_atr_percent', 0.05), config.get('atr_filter.max_atr_percent', 0.5)
            self.market_phase = "НОРМАЛЬНАЯ ВОЛАТИЛЬНОСТЬ"

        self.cluster_analysis_enabled = config.get('cluster_analysis.enabled', False)
        if self.cluster_analysis_enabled:
            self.cluster_time_window = timedelta(seconds=config.get('cluster_analysis.time_window_sec', 15))
            self.cluster_min_volume = config.get('cluster_analysis.min_total_volume', 20)
            self.cluster_imbalance_ratio = config.get('cluster_analysis.imbalance_ratio', 3.0)
            self.cluster_max_to_show = config.get('cluster_analysis.max_clusters_to_show', 5)
            self.recent_trades_for_clusters, self.hot_clusters = deque(), {}

        self.absorption_analysis_enabled = config.get('absorption_analysis.enabled', False)
        if self.absorption_analysis_enabled:
            self.min_iceberg_size = config.get('absorption_analysis.min_size_to_track', 500)
            self.absorption_trigger_ratio = config.get('absorption_analysis.trigger_volume_ratio', 0.5)
            self.absorption_window = timedelta(seconds=config.get('absorption_analysis.time_window_sec', 60))
            self.tracked_icebergs = {}

        self.imbalance_analysis_enabled = config.get('orderbook_imbalance.enabled', False)
        if self.imbalance_analysis_enabled:
            self.imbalance_depth = config.get('orderbook_imbalance.depth_to_analyze', 5)
            self.strong_imbalance_ratio, self.slight_imbalance_ratio = config.get('orderbook_imbalance.strong_imbalance_ratio', 2.5), config.get('orderbook_imbalance.slight_imbalance_ratio', 1.5)
            self.last_imbalance_ratio = 1.0

        self.profile_analysis_enabled = config.get('intraday_profile.enabled', False)
        if self.profile_analysis_enabled:
            self.activity_profile_file = config.get('intraday_profile.profile_file', 'activity_profile.json')
            self.profile_update_interval = timedelta(minutes=config.get('intraday_profile.update_interval_min', 15))
            self.anomaly_threshold = config.get('intraday_profile.anomaly_threshold_ratio', 1.75)
            self.activity_profile = self.load_activity_profile()
            self.last_profile_update, self.current_interval_ticks, self.current_interval_volume, self.intensity_ratio = datetime.now(), 0, 0, 1.0

        self.time_and_sales_enabled = config.get('time_and_sales.enabled', False)
        if self.time_and_sales_enabled:
            self.max_trades_to_show, self.min_volume_to_show = config.get('time_and_sales.max_trades_to_show', 10), config.get('time_and_sales.min_volume_to_show', 1)
            self.recent_all_trades = deque(maxlen=self.max_trades_to_show * 2)

        self.order_flow_analysis_enabled = config.get('order_flow_analysis.enabled', False)
        if self.order_flow_analysis_enabled:
            self.imbalance_sequence_length, self.imbalance_max_price_diff = config.get('order_flow_analysis.imbalance_sequence_length', 3), config.get('order_flow_analysis.imbalance_max_price_diff', 1)
        
        self.flow_toxicity_analysis_enabled = config.get('flow_toxicity_analysis.enabled', True)
        if self.flow_toxicity_analysis_enabled:
            self.flow_analysis_window = timedelta(seconds=60)
            self.flow_trades = deque()
            self.avg_trade_size, self.trade_rate, self.hft_marker_threshold = 0, 0, 1.5

        self.atr_dynamics_enabled = True
        if self.atr_dynamics_enabled:
            self.atr_history = {tf: deque(maxlen=10) for tf in self.timeframes}

        self.level_memory_enabled = config.get('level_memory.enabled', True)
        if self.level_memory_enabled:
            self.historical_levels = {'poc': deque(maxlen=5), 'vah': deque(maxlen=5), 'val': deque(maxlen=5)}

        self.vwap_enabled = config.get('vwap.enabled', True)
        if self.vwap_enabled:
            self.vwap = None
            self.cumulative_price_volume = 0
            self.cumulative_volume = 0
            self.current_trading_day = None

        self.orderbook_heatmap_enabled = config.get('orderbook_heatmap.enabled', False)
        if self.orderbook_heatmap_enabled:
            self.heatmap_levels_to_display = config.get('orderbook_heatmap.levels_to_display', 10)
            self.heatmap_display_step = config.get('orderbook_heatmap.display_step', 1)
            self.heatmap_bar_length = config.get('orderbook_heatmap.bar_length', 50)

    def _quik_datetime_to_datetime(self, quik_dt):
        if isinstance(quik_dt, str): return datetime.fromisoformat(quik_dt)
        if isinstance(quik_dt, dict):
            return datetime(year=int(quik_dt.get('year', 0)), month=int(quik_dt.get('month', 0)), day=int(quik_dt.get('day', 0)),
                            hour=int(quik_dt.get('hour', 0)), minute=int(quik_dt.get('min', 0)), second=int(quik_dt.get('sec', 0)))
        return None

    def setup_logger(self):
        logger = logging.getLogger('QuikMonitor')
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%d.%m.%Y %H:%M:%S')
        file_handler = logging.FileHandler('quik_monitor.log', encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        return logger

    def check_connection(self):
        try: return self.qp.is_connected()['data'] == 1
        except Exception as e: self.logger.error(f"Ошибка проверки подключения: {str(e)}"); return False

    def check_market_session(self):
        now = datetime.now().time()
        sessions = [(dt_time(9, 50), dt_time(14, 0), "🟩 Дневная сессия"), (dt_time(14, 0), dt_time(14, 3), "🟧 Дневной клиринг"),
                    (dt_time(14, 3), dt_time(18, 45), "🟩 Вечерняя сессия"), (dt_time(18, 45), dt_time(19, 0), "🟧 Вечерний клиринг"),
                    (dt_time(19, 0), dt_time(23, 50), "🟦 Ночная сессия")]
        for start, end, status in sessions: 
            if start <= now < end: return status
        return "🟥 Вне торговой сессии"

    def load_activity_profile(self):
        try:
            if os.path.exists(self.activity_profile_file):
                with open(self.activity_profile_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e: self.logger.error(f"Ошибка загрузки профиля активности: {e}")
        return {}

    def save_activity_profile(self):
        if not self.profile_analysis_enabled: return
        try:
            with open(self.activity_profile_file, 'w', encoding='utf-8') as f:
                json.dump(self.activity_profile, f, indent=4)
        except Exception as e: self.logger.error(f"Ошибка сохранения профиля активности: {e}")

    def update_activity_profile(self):
        now = datetime.now()
        if now - self.last_profile_update < self.profile_update_interval:
            return
        time_key = self.last_profile_update.strftime('%H:%M')
        profile_data = self.activity_profile.get(time_key, {'avg_ticks': 0, 'avg_volume': 0, 'count': 0})
        new_count = profile_data['count'] + 1
        self.activity_profile[time_key] = {
            'avg_ticks': (profile_data['avg_ticks'] * profile_data['count'] + self.current_interval_ticks) / new_count,
            'avg_volume': (profile_data['avg_volume'] * profile_data['count'] + self.current_interval_volume) / new_count,
            'count': new_count
        }
        self.current_interval_ticks, self.current_interval_volume = 0, 0
        self.last_profile_update = now
        self.save_activity_profile()

    def save_history_cache(self):
        try:
            with self.data_lock:
                cache_data = {'bars': [{'open': b.open, 'high': b.high, 'low': b.low, 'close': b.close, 'volume': b.volume, 'start_time': b.start_time.isoformat()} for b in list(self.bars['1M'])]}
                with open(self.cache_file, 'w') as f: json.dump(cache_data, f)
        except Exception as e: self.logger.error(f"Ошибка сохранения кэша: {str(e)}")

    def load_history_cache(self):
        try:
            if not os.path.exists(self.cache_file): return False
            with open(self.cache_file, 'r') as f: cache_data = json.load(f)
            required_bars = self.config.get('indicators.ema_slow_period', 21) * 2
            for bar_data in cache_data.get('bars', [])[-required_bars:]:
                bar = BarData(open=float(bar_data['open']), high=float(bar_data['high']), low=float(bar_data['low']), close=float(bar_data['close']), volume=int(bar_data['volume']), start_time=self._quik_datetime_to_datetime(bar_data['start_time']))
                self.bars['1M'].append(bar)
            return len(self.bars['1M']) > 0
        except Exception as e: self.logger.error(f"Ошибка загрузки кэша: {str(e)}"); return False

    def load_historical_data(self):
        if self.load_history_cache():
            self.aggregate_all_timeframes_from_history()
            return True
        
        required_bars = self.config.get('indicators.ema_slow_period', 21) * 2
        bars_data = self.qp.get_candles_from_data_source(self.class_code, self.sec_code, 1, required_bars)['data']
        if not bars_data:
            return False
        for bar_data in bars_data:
            bar = BarData(open=float(bar_data['open']), high=float(bar_data['high']), low=float(bar_data['low']), close=float(bar_data['close']), volume=int(bar_data['volume']), start_time=self._quik_datetime_to_datetime(bar_data['datetime']))
            self.bars['1M'].append(bar)
        
        self.aggregate_all_timeframes_from_history()
        self.save_history_cache()
        return True

    def setup_subscriptions(self):
        self.qp.on_quote = self.handle_quote_update
        self.qp.subscribe_level2_quotes(self.class_code, self.sec_code)
        self.qp.on_all_trade = self.handle_trade_update
        self.qp.param_request(self.class_code, self.sec_code, 'ALL_TRADES')

    def handle_quote_update(self, data):
        if time.time() - self.last_quote_time < self.quote_interval: return
        self.last_quote_time = time.time()
        
        if 'data' in data and data['data'].get('sec_code') == self.sec_code:
            with self.data_lock:
                quote_data = data['data']
                self.all_bid_orders = quote_data.get('bid', [])
                self.all_offer_orders = quote_data.get('offer', [])
                if not self.all_bid_orders or not self.all_offer_orders: return
                
                self.top_bids = sorted(self.all_bid_orders, key=lambda x: float(x['price']), reverse=True)[:5]
                self.top_offers = sorted(self.all_offer_orders, key=lambda x: float(x['price']))[:5]
                self.bid_volume = sum(int(o.get('quantity', 0)) for o in self.all_bid_orders)
                self.offer_volume = sum(int(o.get('quantity', 0)) for o in self.all_offer_orders)
                self.bid_levels = len(self.all_bid_orders)
                self.offer_levels = len(self.all_offer_orders)
                self.last_change_time = datetime.now()
                
                self.calculate_oscillator()
                if self.absorption_analysis_enabled: self.update_iceberg_tracker(self.all_bid_orders, self.all_offer_orders)
                if self.imbalance_analysis_enabled: self.calculate_orderbook_imbalance(self.all_bid_orders, self.all_offer_orders)
                self.redraw_needed = True

    def process_trade(self, trade):
        if time.time() - self.last_trade_time < self.trade_interval: return
        self.last_trade_time = time.time()
        if trade.get('sec_code') != self.sec_code: return
        
        trade_time = self._quik_datetime_to_datetime(trade.get('datetime'))
        if not trade_time: return

        with self.data_lock:
            quantity = int(trade.get('qty', 0))
            price = float(trade['price'])
            flags = int(trade.get('flags', 0))

            self.tick_history.append(trade)
            self.last_tick_time = trade_time
            if flags & 1: self.buy_volume += quantity
            elif flags & 2: self.sell_volume += quantity
            self.tick_volume += quantity
            self.tick_count += 1
            
            if len(self.tick_history) > 1:
                prev_price = float(self.tick_history[-2].get('price', 0))
                if price > prev_price: self.price_direction = "↑"
                elif price < prev_price: self.price_direction = "↓"

            self.update_bars(price, quantity, trade_time)
            
            if self.tape_analysis_enabled: self.update_tape_data(trade, trade_time)
            if self.cvd_analysis_enabled: self.update_cvd_data(trade, price)
            if self.cluster_analysis_enabled: self.recent_trades_for_clusters.append(trade)
            if self.absorption_analysis_enabled: self.check_trade_against_icebergs(trade)
            if self.profile_analysis_enabled: self.current_interval_ticks += 1; self.current_interval_volume += quantity
            if self.time_and_sales_enabled: 
                imbalance_tag = self.check_tape_imbalance(price, flags)
                self.recent_all_trades.append({'price': price, 'volume': quantity, 'timestamp': trade_time, 'direction': "Покупка" if flags & 1 else "Продажа", 'imbalance': imbalance_tag})
            
            self.redraw_needed = True

    def handle_trade_update(self, data):
        if 'data' in data: self.process_trade(data['data'])

    def update_tape_data(self, trade, trade_time):
        quantity = int(trade.get('qty', 0))
        if quantity >= self.large_lot_threshold:
            self.large_trades.append(trade)
            self.alert_system.trigger_alert(f"large_trade_{trade_time.isoformat()}", f"🔔 КРУПНАЯ СДЕЛКА! {'Покупка' if int(trade.get('flags', 0)) & 1 else 'Продажа'} {quantity} @ {trade['price']}", alert_type="large_trade")
        
        self.recent_trades.append(trade_time)
        while self.recent_trades and (trade_time - self.recent_trades[0] > self.tape_speed_interval):
            self.recent_trades.popleft()
        self.tape_speed = len(self.recent_trades) / self.tape_speed_interval.total_seconds()
        
        if self.tape_speed > self.tape_speed_alert_tps:
            self.alert_system.trigger_alert("tape_speed_alert", f"🚀 УСКОРЕНИЕ ЛЕНТЫ! {self.tape_speed:.1f} т/с", alert_type="tape_speed_alert")
        else:
            self.alert_system.clear_alert("tape_speed_alert")

    def update_cvd_data(self, trade, price):
        quantity = int(trade.get('qty', 0))
        flags = int(trade.get('flags', 0))
        if flags & 1: self.cumulative_delta += quantity
        elif flags & 2: self.cumulative_delta -= quantity
        
        if self.session_max_cvd is None or self.cumulative_delta > self.session_max_cvd: self.session_max_cvd = self.cumulative_delta
        if self.session_min_cvd is None or self.cumulative_delta < self.session_min_cvd: self.session_min_cvd = self.cumulative_delta
        
        self.cvd_history.append(self.cumulative_delta)
        self.price_history_for_cvd.append(price)
        
        if len(self.price_history_for_cvd) > self.divergence_lookback:
            new_divergence = market_analyzer.check_cvd_divergence(self.price_history_for_cvd, self.cvd_history, self.divergence_lookback)
            if new_divergence: self.active_divergence = new_divergence

    def update_iceberg_tracker(self, bids, offers):
        now = datetime.now()
        current_icebergs = {float(o['price']): 'offer' for o in offers if int(o['quantity']) >= self.min_iceberg_size}
        current_icebergs.update({float(b['price']): 'bid' for b in bids if int(b['quantity']) >= self.min_iceberg_size})
        
        for price, type in current_icebergs.items():
            if price not in self.tracked_icebergs:
                size = int(next((o.get('quantity') for o in offers if float(o.get('price')) == price), next((b.get('quantity') for b in bids if float(b.get('price')) == price), '0')))
                self.tracked_icebergs[price] = {'type': type, 'initial_size': size, 'absorbed_volume': 0, 'start_time': now, 'absorption_history': deque(maxlen=50)}
        
        stale_icebergs = [p for p, d in self.tracked_icebergs.items() if p not in current_icebergs or (now - d['start_time'] > self.absorption_window)]
        for price in stale_icebergs:
            del self.tracked_icebergs[price]

    def check_trade_against_icebergs(self, trade):
        price = float(trade['price'])
        if price in self.tracked_icebergs:
            iceberg = self.tracked_icebergs[price]
            if (int(trade.get('flags', 0)) & 1 and iceberg['type'] == 'offer') or (int(trade.get('flags', 0)) & 2 and iceberg['type'] == 'bid'):
                iceberg['absorbed_volume'] += int(trade.get('qty', 0))
                iceberg['absorption_history'].append((datetime.now(), int(trade.get('qty', 0))))

    def evaluate_absorption_alerts(self):
        for price, data in self.tracked_icebergs.items():
            key = f"absorption_{price}"
            required_volume = data['initial_size'] * self.absorption_trigger_ratio
            if data['absorbed_volume'] >= required_volume:
                message = f"🛡️ ПОГЛОЩЕНИЕ {'ПОКУПОК' if data['type'] == 'offer' else 'ПРОДАЖ'}! Уровень {price:.2f}."
                self.alert_system.trigger_alert(key, message, alert_type=f"absorption_{'buy' if data['type'] == 'offer' else 'sell'}")

    def calculate_orderbook_imbalance(self, bids, offers):
        top_bids_volume = sum(int(o['quantity']) for o in sorted(bids, key=lambda x: float(x['price']), reverse=True)[:self.imbalance_depth])
        top_offers_volume = sum(int(o['quantity']) for o in sorted(offers, key=lambda x: float(x['price']))[:self.imbalance_depth])
        if top_offers_volume > 0: self.last_imbalance_ratio = top_bids_volume / top_offers_volume
        else: self.last_imbalance_ratio = self.strong_imbalance_ratio

    def check_tape_imbalance(self, current_price, current_flags):
        if not self.order_flow_analysis_enabled or not self.recent_all_trades: return None
        current_direction = 'buy' if current_flags & 1 else 'sell'
        sequence_trades = list(self.recent_all_trades)[-(self.imbalance_sequence_length - 1):]
        if len(sequence_trades) < self.imbalance_sequence_length - 1: return None
        is_imbalance = all( (trade['direction'] == ("Покупка" if current_direction == 'buy' else "Продажа")) and (abs(current_price - trade['price']) <= self.imbalance_max_price_diff) for trade in sequence_trades)
        if is_imbalance: return f"АГРЕССИЯ {'ПОКУПАТЕЛЕЙ' if current_direction == 'buy' else 'ПРОДАВЦОВ'}"
        return None

    def update_bars(self, price, quantity, trade_time):
        tf = '1M'
        current_bar = self.current_bars.get(tf)
        if current_bar is None:
            self.current_bars[tf] = BarData(price, price, price, price, quantity, trade_time.replace(second=0, microsecond=0))
        else:
            if trade_time >= current_bar.start_time + timedelta(minutes=1):
                self.bars[tf].append(current_bar)
                self.update_indicators_with_new_bar(tf)
                self.update_aggregated_bars(current_bar)
                self.current_bars[tf] = BarData(price, price, price, price, quantity, trade_time.replace(second=0, microsecond=0))
            else:
                current_bar.high = max(current_bar.high, price)
                current_bar.low = min(current_bar.low, price)
                current_bar.close = price
                current_bar.volume += quantity

    def update_aggregated_bars(self, last_1m_bar):
        for tf_str in self.timeframes:
            if tf_str == '1M': continue
            tf_minutes = int(tf_str[:-1])
            current_agg_bar = self.current_bars.get(tf_str)
            if current_agg_bar is None:
                start_time = last_1m_bar.start_time - timedelta(minutes=last_1m_bar.start_time.minute % tf_minutes)
                self.current_bars[tf_str] = BarData(last_1m_bar.open, last_1m_bar.high, last_1m_bar.low, last_1m_bar.close, last_1m_bar.volume, start_time)
            else:
                if last_1m_bar.start_time >= current_agg_bar.start_time + timedelta(minutes=tf_minutes):
                    self.bars[tf_str].append(current_agg_bar)
                    self.update_indicators_with_new_bar(tf_str)
                    start_time = last_1m_bar.start_time - timedelta(minutes=last_1m_bar.start_time.minute % tf_minutes)
                    self.current_bars[tf_str] = BarData(last_1m_bar.open, last_1m_bar.high, last_1m_bar.low, last_1m_bar.close, last_1m_bar.volume, start_time)
                else:
                    current_agg_bar.high = max(current_agg_bar.high, last_1m_bar.high)
                    current_agg_bar.low = min(current_agg_bar.low, last_1m_bar.low)
                    current_agg_bar.close = last_1m_bar.close
                    current_agg_bar.volume += last_1m_bar.volume

    def aggregate_all_timeframes_from_history(self):
        for tf_str in self.timeframes:
            if tf_str != '1M': self._rebuild_aggregated_tf_from_history(int(tf_str[:-1]))

    def _rebuild_aggregated_tf_from_history(self, tf_minutes):
        tf_str = f"{tf_minutes}M"
        source_bars = list(self.bars['1M'])
        if not source_bars: return
        self.bars[tf_str].clear()
        self.current_bars[tf_str] = None
        current_agg_bar = None
        for bar in source_bars:
            tf_start_time = bar.start_time - timedelta(minutes=bar.start_time.minute % tf_minutes)
            if current_agg_bar and current_agg_bar.start_time != tf_start_time:
                self.bars[tf_str].append(current_agg_bar)
                current_agg_bar = None
            if current_agg_bar is None:
                current_agg_bar = BarData(bar.open, bar.high, bar.low, bar.close, bar.volume, tf_start_time)
            else:
                current_agg_bar.high = max(current_agg_bar.high, bar.high)
                current_agg_bar.low = min(current_agg_bar.low, bar.low)
                current_agg_bar.close = bar.close
                current_agg_bar.volume += bar.volume
        if current_agg_bar: self.bars[tf_str].append(current_agg_bar)

    def calculate_oscillator(self):
        total = self.bid_volume + self.offer_volume
        if total > 0:
            osc_value = (self.bid_volume - self.offer_volume) / total * 100
            self.oscillator_value = 0.7 * self.last_oscillator + 0.3 * osc_value
            self.last_oscillator = self.oscillator_value

    def calculate_all_indicators_from_history(self):
        with self.data_lock:
            for tf in self.timeframes:
                self.update_indicators_with_new_bar(tf)

    def calculate_bb_for_tf(self, tf, bars):
        calculator = self.indicator_calculators[tf]
        if len(bars) < calculator.bb_period:
            return None, None, None, False
        closes = np.array([bar.close for bar in bars[-calculator.bb_period:]])
        sma = np.mean(closes)
        std_dev = np.std(closes)
        bb_upper = sma + (std_dev * calculator.bb_std_dev)
        bb_lower = sma - (std_dev * calculator.bb_std_dev)
        bb_width = bb_upper - bb_lower
        atr = self.indicators[tf].atr
        is_squeeze = (atr is not None and bb_width < (atr * calculator.bb_squeeze_threshold))
        return bb_upper, sma, bb_lower, is_squeeze

    def calculate_volume_profile(self, tf, bars):
        calculator = self.indicator_calculators[tf]
        period_bars = bars[-calculator.vp_period:]
        if len(period_bars) < 2:
            return None, None, None
        price_step = self.qp.get_symbol_info(self.class_code, self.sec_code).get('min_price_step')
        if price_step is None or price_step <= 0:
            return None, None, None
        highs = np.array([b.high for b in period_bars])
        lows = np.array([b.low for b in period_bars])
        volumes = np.array([b.volume for b in period_bars])
        min_price_overall = np.min(lows)
        max_price_overall = np.max(highs)
        num_levels = int((max_price_overall - min_price_overall) / price_step) + 1
        price_levels = min_price_overall + np.arange(num_levels) * price_step
        volume_at_price = np.zeros(num_levels)
        start_indices = np.round((lows - min_price_overall) / price_step).astype(int)
        end_indices = np.round((highs - min_price_overall) / price_step).astype(int)
        for i in range(len(period_bars)):
            bar_levels = end_indices[i] - start_indices[i] + 1
            vol_per_level = volumes[i] / bar_levels if bar_levels > 0 else volumes[i]
            np.add.at(volume_at_price, np.arange(start_indices[i], end_indices[i] + 1), vol_per_level)
        if np.sum(volume_at_price) == 0:
            return None, None, None
        poc_index = np.argmax(volume_at_price)
        poc = price_levels[poc_index]
        total_volume = np.sum(volume_at_price)
        target_volume = total_volume * (calculator.vp_value_area_percent / 100)
        sorted_indices = np.argsort(-volume_at_price)
        cumulative_volume = np.cumsum(volume_at_price[sorted_indices])
        va_indices = sorted_indices[cumulative_volume <= target_volume]
        if len(va_indices) == 0:
            va_indices = [poc_index]
        va_prices = price_levels[va_indices]
        vah = np.max(va_prices)
        val = np.min(va_prices)
        return poc, vah, val

    def update_indicators_with_new_bar(self, tf):
        with self.data_lock:
            bars = list(self.bars[tf])
            if not bars: return
            last_bar = bars[-1]
            calculator = self.indicator_calculators[tf]
            indicator_data = self.indicators[tf]
            
            fast_ema, slow_ema = calculator.update_ema(last_bar.close)
            indicator_data.fast_ema, indicator_data.slow_ema = fast_ema, slow_ema
            indicator_data.rsi, indicator_data.rsi_upper_band, indicator_data.rsi_middle_band, indicator_data.rsi_lower_band = calculator.update_rsi(last_bar.close)
            indicator_data.atr = calculator.update_atr(last_bar.high, last_bar.low, last_bar.close)
            if calculator.bb_enabled:
                indicator_data.bb_upper, indicator_data.bb_middle, indicator_data.bb_lower, indicator_data.is_squeeze = self.calculate_bb_for_tf(tf, bars)
            if calculator.vp_enabled:
                indicator_data.poc, indicator_data.vah, indicator_data.val = self.calculate_volume_profile(tf, bars)
            indicator_data.vsa_status = market_analyzer.analyze_vsa(bars)
            
            if self.atr_dynamics_enabled and indicator_data.atr is not None:
                self.atr_history[tf].append(indicator_data.atr)

        self.evaluate_all_signals()

    def calculate_trade_parameters(self, direction, entry_price):
        params_config = self.config.get('trade_parameters', {})
        if not params_config.get('enabled'):
            return None, None

        method = params_config.get('method')
        atr_tf = params_config.get('atr_tf', '5M')
        atr_multiplier_sl = params_config.get('atr_multiplier_sl', 1.5)
        atr_multiplier_tp = params_config.get('atr_multiplier_tp', 3.0)

        atr_indicator = self.indicators.get(atr_tf)
        vp_indicator = self.indicators.get(atr_tf) # Используем тот же ТФ для VP

        if not atr_indicator or atr_indicator.atr is None:
            return None, None # Не можем рассчитать без ATR

        stop_loss, take_profit = None, None

        if method == 'ATR_VP':
            atr_value = atr_indicator.atr
            if direction == 'long':
                stop_loss = entry_price - (atr_value * atr_multiplier_sl)
                if vp_indicator and vp_indicator.vah and vp_indicator.vah > entry_price:
                    take_profit = vp_indicator.vah
                elif vp_indicator and vp_indicator.poc and vp_indicator.poc > entry_price:
                    take_profit = vp_indicator.poc
                else:
                    take_profit = entry_price + (atr_value * atr_multiplier_tp)
            
            elif direction == 'short':
                stop_loss = entry_price + (atr_value * atr_multiplier_sl)
                if vp_indicator and vp_indicator.val and vp_indicator.val < entry_price:
                    take_profit = vp_indicator.val
                elif vp_indicator and vp_indicator.poc and vp_indicator.poc < entry_price:
                    take_profit = vp_indicator.poc
                else:
                    take_profit = entry_price - (atr_value * atr_multiplier_tp)

        price_step = self.qp.get_symbol_info(self.class_code, self.sec_code).get('min_price_step', 1)
        if stop_loss:
            stop_loss = round(stop_loss / price_step) * price_step
        if take_profit:
            take_profit = round(take_profit / price_step) * price_step

        return stop_loss, take_profit

    def evaluate_all_signals(self):
        self.evaluate_market_phase()

    def evaluate_market_phase(self):
        if not self.atr_filter_enabled: return
        tf = self.atr_filter_tf
        indicators = self.indicators.get(tf)
        bars = list(self.bars.get(tf, []))
        if not indicators or not bars or indicators.atr is None: self.market_phase = "ОЦЕНКА"; return
        last_price = bars[-1].close
        if last_price == 0: self.market_phase = "ОЦЕНКА"; return
        atr_percent = (indicators.atr / last_price) * 100
        if atr_percent < self.min_atr_percent: self.market_phase = "НИЗКАЯ ВОЛАТИЛЬНОСТЬ"
        elif atr_percent > self.max_atr_percent: self.market_phase = "ЭКСТРЕМАЛЬНАЯ ВОЛАТИЛЬНОСТЬ"
        else: self.market_phase = "НОРМАЛЬНАЯ ВОЛАТИЛЬНОСТЬ"

    def _perform_startup_check(self):
        print("="*80)
        print("ПРОВЕРКА СТАТУСА МОДУЛЕЙ АНАЛИЗА И ОТОБРАЖЕНИЯ")
        print("-" * 80)
        
        modules = {
            # Аналитические модули
            "Анализ ленты (Tape)": 'tape_analysis_enabled',
            "Кумулятивная дельта (CVD)": 'cvd_analysis_enabled',
            "Фильтр волатильности (ATR)": 'atr_filter_enabled',
            "Кластерный анализ": 'cluster_analysis_enabled',
            "Анализ поглощений (Icebergs)": 'absorption_analysis_enabled',
            "Дисбаланс в стакане": 'imbalance_analysis_enabled',
            "Профиль активности интрадей": 'profile_analysis_enabled',
            "Анализ потока ордеров": 'order_flow_analysis_enabled',
            "Анализ токсичности потока": 'flow_toxicity_analysis_enabled',
            "Память уровней": 'level_memory_enabled',
            "Расчет VWAP": 'vwap_enabled',
            "Расчет параметров сделки": self.config.get('trade_parameters.enabled', False),
            "Обнаружение спуфинга": self.config.get('absorption_analysis.detect_spoofing', False),
            "Комплексные сигналы": self.config.get('complex_signal_rules.enabled', False),
            # Визуальные модули
            "Лента сделок (Time & Sales)": 'time_and_sales_enabled',
            "Тепловая карта стакана": 'orderbook_heatmap_enabled',
            "Контроль таймфреймов": True,
            "Индикаторы рынка (EMA, RSI, etc)": True,
            "Осциллятор Спроса/Предложения": True,
            "Тиковая активность": True,
        }

        enabled_count = 0
        print(f"{'Название модуля':<38} | Статус")
        print("-" * 80)
        for name, attr in modules.items():
            if isinstance(attr, str):
                status = getattr(self, attr, False)
            else:
                status = attr
            
            status_str = f"{Fore.GREEN}[✓] ВКЛЮЧЕНО{Style.RESET_ALL}" if status else f"{Fore.RED}[✗] ВЫКЛЮЧЕНО{Style.RESET_ALL}"
            print(f"  {name:<35} | {status_str}")
            if status:
                enabled_count += 1
        
        print("-" * 80)
        print(f"ИТОГО: {enabled_count} из {len(modules)} модулей активно.")
        print("="*80)
        input("Нажмите Enter для продолжения...")

    def run(self):
        if not self.check_connection(): self.logger.error("QUIK connection failed."); return
        if not self.load_historical_data(): self.logger.error("Historical data loading failed."); return
        
        self._perform_startup_check()

        self.calculate_all_indicators_from_history()
        self.setup_subscriptions()
        
        self.logger.info("Monitoring started...")
        try:
            while self.running:
                with self.data_lock:
                    if self.cluster_analysis_enabled:
                        self.hot_clusters = market_analyzer.update_clusters(self.recent_trades_for_clusters, self.cluster_time_window, self.cluster_min_volume)
                    if self.absorption_analysis_enabled: self.evaluate_absorption_alerts()
                    if self.profile_analysis_enabled: self.update_activity_profile()

                if self.redraw_needed:
                    self.dashboard.display_dashboard()
                    self.redraw_needed = False
                
                time.sleep(self.update_interval)
        except KeyboardInterrupt:
            self.logger.info("Monitoring stopped by user.")
        finally:
            self.stop()

    def stop(self):
        self.running = False
        self.save_history_cache()
        if self.profile_analysis_enabled:
            self.save_activity_profile()
        self.logger.info("Monitoring stopped.")
