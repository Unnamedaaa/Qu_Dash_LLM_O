# -*- coding: utf-8 -*-
from collections import deque
import numpy as np

from collections import deque
import numpy as np
from datetime import datetime, timedelta

class OrderFlowAnalyzer:
    """Анализирует поток сделок для расчета метрик потока ордеров."""
    def __init__(self, config):
        # CVD
        self.cumulative_delta = 0
        self.cvd_history = deque(maxlen=config.get('cvd.period', 200))
        self.price_history_for_cvd = deque(maxlen=config.get('cvd.period', 200))
        self.divergence_lookback = config.get('cvd.divergence_lookback', 60)

        # Clusters
        self.recent_trades_for_clusters = deque()
        self.cluster_time_window = timedelta(seconds=config.get('clusters.time_window_sec', 15))
        self.cluster_min_volume = config.get('clusters.min_total_volume', 20)

        # Icebergs
        self.min_iceberg_size = config.get('icebergs.min_size_to_track', 500)
        self.absorption_trigger_ratio = config.get('icebergs.trigger_volume_ratio', 0.5)
        self.absorption_window = timedelta(seconds=config.get('icebergs.time_window_sec', 60))
        self.tracked_icebergs = {}
        self.absorption_events = deque(maxlen=5)

    def process_trade(self, trade_data: dict):
        """Обрабатывает сделку для обновления CVD и проверки поглощения айсбергов."""
        self.add_trade_for_cluster_analysis(trade_data)
        self._update_cvd(trade_data)
        self._check_trade_against_icebergs(trade_data)

    def process_order_book(self, bids: list, offers: list):
        """Обрабатывает обновление стакана для отслеживания айсберг-заявок."""
        now = datetime.now()
        current_icebergs = {float(o['price']): {'type': 'offer', 'volume': int(o['quantity'])} for o in offers if int(o['quantity']) >= self.min_iceberg_size}
        current_icebergs.update({float(b['price']): {'type': 'bid', 'volume': int(b['quantity'])} for b in bids if int(b['quantity']) >= self.min_iceberg_size})

        # Удаляем старые или исчезнувшие айсберги
        stale_icebergs = [p for p, d in self.tracked_icebergs.items() if p not in current_icebergs or (now - d['start_time'] > self.absorption_window)]
        for price in stale_icebergs:
            del self.tracked_icebergs[price]

        # Добавляем новые
        for price, data in current_icebergs.items():
            if price not in self.tracked_icebergs:
                self.tracked_icebergs[price] = {
                    'type': data['type'],
                    'initial_size': data['volume'],
                    'absorbed_volume': 0,
                    'start_time': now
                }

    def _check_trade_against_icebergs(self, trade_data: dict):
        price = float(trade_data['price'])
        if price in self.tracked_icebergs:
            iceberg = self.tracked_icebergs[price]
            trade_is_buy = int(trade_data.get('flags', 0)) & 1
            
            # Если покупка бьет в лимитку на продажу (айсберг-оффер) или продажа в айсберг-бид
            if (trade_is_buy and iceberg['type'] == 'offer') or (not trade_is_buy and iceberg['type'] == 'bid'):
                iceberg['absorbed_volume'] += int(trade_data['qty'])
                # Проверяем, не поглотили ли заявку
                if iceberg['absorbed_volume'] >= iceberg['initial_size'] * self.absorption_trigger_ratio:
                    event_text = f"АТАКА НА АЙСБЕРГ ({iceberg['type']}) @ {price:.2f}"
                    self.absorption_events.append(event_text)
                    # Удаляем, чтобы не триггерить повторно
                    if price in self.tracked_icebergs: del self.tracked_icebergs[price]

    def _update_cvd(self, trade_data: dict):
        """Обновляет CVD на основе новой сделки."""


    def check_divergence(self) -> str:
        """Проверяет наличие бычьей или медвежьей дивергенции между ценой и CVD."""
        if len(self.price_history_for_cvd) < self.divergence_lookback:
            return "Нет"

        prices = np.array(list(self.price_history_for_cvd)[-self.divergence_lookback:])
        cvd = np.array(list(self.cvd_history)[-self.divergence_lookback:])

        # Ищем последние два локальных минимума/максимума
        price_local_max_idx = np.argmax(prices)
        price_local_min_idx = np.argmin(prices)
        cvd_local_max_idx = np.argmax(cvd)
        cvd_local_min_idx = np.argmin(cvd)

        # Медвежья дивергенция: новый максимум цены НЕ подтвержден новым максимумом CVD
        if price_local_max_idx > cvd_local_max_idx + 5: # +5 для допуска
            # Находим предыдущий максимум цены
            prev_prices = prices[:price_local_max_idx]
            if len(prev_prices) > 10:
                prev_price_max_idx = np.argmax(prev_prices)
                if prices[price_local_max_idx] > prices[prev_price_max_idx] and cvd[price_local_max_idx] < cvd[prev_price_max_idx]:
                    return "Медвежья"

        # Бычья дивергенция: новый минимум цены НЕ подтвержден новым минимумом CVD
        if price_local_min_idx > cvd_local_min_idx + 5:
            # Находим предыдущий минимум цены
            prev_prices = prices[:price_local_min_idx]
            if len(prev_prices) > 10:
                prev_price_min_idx = np.argmin(prev_prices)
                if prices[price_local_min_idx] < prices[prev_price_min_idx] and cvd[price_local_min_idx] > cvd[prev_price_min_idx]:
                    return "Бычья"

        return "Нет"

    def update_clusters(self):
        """Анализирует недавние сделки для поиска горячих кластеров."""
        now = datetime.now()
        
        # Очищаем очередь от старых сделок
        while self.recent_trades_for_clusters and (now - self.recent_trades_for_clusters[0]['time'] > self.cluster_time_window):
            self.recent_trades_for_clusters.popleft()

        clusters = {}
        for trade in self.recent_trades_for_clusters:
            price = float(trade['price'])
            quantity = int(trade['qty'])
            flags = int(trade.get('flags', 0))
            
            if price not in clusters:
                clusters[price] = {'buy': 0, 'sell': 0, 'total': 0}
            
            if flags & 1: # Покупка
                clusters[price]['buy'] += quantity
            elif flags & 2: # Продажа
                clusters[price]['sell'] += quantity
            
            clusters[price]['total'] += quantity

        # Фильтруем и сортируем кластеры
        significant_clusters = {p: v for p, v in clusters.items() if v['total'] >= self.cluster_min_volume}
        # Возвращаем отсортированный список кластеров
        return sorted([{'price': p, 'volume': v['total'], 'buy_volume': v['buy'], 'sell_volume': v['sell']} for p, v in significant_clusters.items()], key=lambda x: x['volume'], reverse=True)
