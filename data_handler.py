# -*- coding: utf-8 -*-
import os
import logging
import json
from datetime import datetime, timedelta
from collections import deque
import pandas as pd

class BarData:
    """Класс для хранения данных одной свечи."""
    def __init__(self, open, high, low, close, volume, start_time):
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.start_time = start_time

    def to_dict(self):
        return {
            'datetime': self.start_time.isoformat(),
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume
        }

class DataHandler:
    """Отвечает за загрузку, сохранение и обновление данных на основе тиков."""
    def __init__(self, quik_connector, class_code, sec_code, timeframes):
        self.logger = logging.getLogger('DataHandler')
        self.quik = quik_connector
        self.class_code = class_code
        self.sec_code = sec_code
        self.timeframes = timeframes
        self.data_path = os.path.join(os.path.dirname(__file__), 'data')
        if not os.path.exists(self.data_path):
            os.makedirs(self.data_path)
        self.cache_file = os.path.join(self.data_path, f'{self.class_code}_{self.sec_code}_1M.json')
        self.last_bar_time = None

        # Используем deque для эффективного добавления и ограничения размера
        self.bars = {tf: deque(maxlen=1000) for tf in self.timeframes}
        self.current_bars = {tf: None for tf in self.timeframes}

    def save_to_cache(self):
        """Сохраняет текущие 1M свечи в кеш."""
        self.logger.info(f"Сохранение кеша в {self.cache_file}...")
        try:
            with open(self.cache_file, 'w') as f:
                json.dump([bar.to_dict() for bar in self.bars['1M']], f)
        except Exception as e:
            self.logger.error(f"Ошибка сохранения кеша: {e}")

    def load_from_cache(self) -> bool:
        """Загружает 1M свечи из кеша, если он существует."""
        if not os.path.exists(self.cache_file):
            return False
        
        self.logger.info(f"Загрузка истории из кеша {self.cache_file}...")
        try:
            with open(self.cache_file, 'r') as f:
                bars_data = json.load(f)
                for bar_data in bars_data:
                    bar = BarData(
                        open=float(bar_data['open']), high=float(bar_data['high']), 
                        low=float(bar_data['low']), close=float(bar_data['close']), 
                        volume=int(bar_data['volume']), 
                        start_time=datetime.fromisoformat(bar_data['datetime'])
                    )
                    self.bars['1M'].append(bar)
                
                if self.bars['1M']:
                    self.last_bar_time = self.bars['1M'][-1].start_time
                self.logger.info(f"Загружено {len(self.bars['1M'])} 1M свечей из кеша.")
                return True
        except Exception as e:
            self.logger.error(f"Ошибка загрузки кеша: {e}")
            return False

    def get_bars_as_dataframe(self, timeframe: str) -> pd.DataFrame:
        """Возвращает данные для указанного таймфрейма в виде pandas DataFrame."""
        if timeframe not in self.bars:
            return pd.DataFrame()
        
        bars_list = [bar.to_dict() for bar in self.bars[timeframe]]
        if not bars_list:
            return pd.DataFrame()

        df = pd.DataFrame(bars_list)
        df.set_index('datetime', inplace=True)
        return df

    def _quik_datetime_to_datetime(self, quik_dt):
        return datetime(year=int(quik_dt.get('year', 0)), month=int(quik_dt.get('month', 0)), day=int(quik_dt.get('day', 0)),
                        hour=int(quik_dt.get('hour', 0)), minute=int(quik_dt.get('min', 0)), second=int(quik_dt.get('sec', 0)))

    def initial_backfill(self):
        """Выполняет полную первоначальную загрузку истории (1M) и агрегирует ее."""
        if not self.load_from_cache() or (self.last_bar_time and self.last_bar_time < datetime.now() - timedelta(days=1)):
            self.logger.info("--- Полная первоначальная загрузка истории (1M) ---")
            # Запрашиваем с запасом для расчета индикаторов
            history_1m_raw = self.quik.get_candles(self.class_code, self.sec_code, '1M', count=1000)
            if not history_1m_raw:
                self.logger.error("Не удалось загрузить первоначальную историю 1M.")
                return
            
            self.bars['1M'].clear()
            for bar_data in history_1m_raw:
                bar = BarData(
                    open=float(bar_data['open']), high=float(bar_data['high']), 
                    low=float(bar_data['low']), close=float(bar_data['close']), 
                    volume=int(bar_data['volume']), 
                    start_time=self._quik_datetime_to_datetime(bar_data['datetime'])
                )
                self.bars['1M'].append(bar)
            self.save_to_cache()
        else:
            # Дозагрузка недостающих данных
            self.logger.info("--- Дозагрузка истории (1M) ---")
            # Рассчитываем, сколько свечей нужно дозагрузить
            time_diff_minutes = (datetime.now() - self.last_bar_time).total_seconds() / 60
            candles_to_load = int(time_diff_minutes)
            if candles_to_load > 0:
                history_1m_raw = self.quik.get_candles(self.class_code, self.sec_code, '1M', count=candles_to_load)
                if not history_1m_raw:
                    self.logger.error("Не удалось дозагрузить историю 1M.")
                else:
                    for bar_data in history_1m_raw:
                        bar_time = self._quik_datetime_to_datetime(bar_data['datetime'])
                        if bar_time > self.last_bar_time:
                            bar = BarData(
                                open=float(bar_data['open']), high=float(bar_data['high']), 
                                low=float(bar_data['low']), close=float(bar_data['close']), 
                                volume=int(bar_data['volume']), 
                                start_time=bar_time
                            )
                            self.bars['1M'].append(bar)
                    self.save_to_cache()

        if self.bars['1M']:
            self.last_bar_time = self.bars['1M'][-1].start_time

        self.logger.info(f"Загружено {len(self.bars['1M'])} 1M свечей. Агрегация старших таймфреймов...")
        self._aggregate_history()
        self.logger.info("Агрегация истории завершена.")

    def _aggregate_history(self):
        """Строит старшие ТФ на основе загруженной истории 1M."""
        for tf_str in self.timeframes:
            if tf_str == '1M': continue
            tf_minutes = int(tf_str[:-1])
            self.bars[tf_str].clear()
            
            source_bars = list(self.bars['1M'])
            if not source_bars: continue

            current_agg_bar = None
            for bar in source_bars:
                # Определяем начало временного слота для старшего ТФ
                agg_start_time = bar.start_time - timedelta(minutes=bar.start_time.minute % tf_minutes, seconds=bar.start_time.second, microseconds=bar.start_time.microsecond)
                
                if current_agg_bar and current_agg_bar.start_time != agg_start_time:
                    self.bars[tf_str].append(current_agg_bar)
                    current_agg_bar = None
                
                if current_agg_bar is None:
                    current_agg_bar = BarData(bar.open, bar.high, bar.low, bar.close, bar.volume, agg_start_time)
                else:
                    current_agg_bar.high = max(current_agg_bar.high, bar.high)
                    current_agg_bar.low = min(current_agg_bar.low, bar.low)
                    current_agg_bar.close = bar.close
                    current_agg_bar.volume += bar.volume
            
            if current_agg_bar: # Добавляем последнюю, возможно неполную, свечу
                self.current_bars[tf_str] = current_agg_bar

    def process_new_trade(self, trade_data: dict) -> list[str]:
        """Обрабатывает новый тик, обновляет свечи и возвращает список закрытых ТФ."""
        try:
            price = float(trade_data['price'])
            quantity = int(trade_data['qty'])
            trade_time = self._quik_datetime_to_datetime(trade_data['datetime'])
            if not trade_time: return []

            closed_timeframes = []

            # --- Обновление 1M свечи ---
            tf_1m = '1M'
            current_1m_bar = self.current_bars.get(tf_1m)
            bar_start_time = trade_time.replace(second=0, microsecond=0)

            if not current_1m_bar:
                self.current_bars[tf_1m] = BarData(price, price, price, price, quantity, bar_start_time)
                return []

            if bar_start_time > current_1m_bar.start_time:
                # Закрываем старую 1M свечу
                self.bars[tf_1m].append(current_1m_bar)
                closed_timeframes.append(tf_1m)
                # Используем закрытую 1M свечу для обновления старших ТФ
                closed_higher_tfs = self._update_aggregated_bars(current_1m_bar)
                closed_timeframes.extend(closed_higher_tfs)
                # Создаем новую 1M свечу
                self.current_bars[tf_1m] = BarData(price, price, price, price, quantity, bar_start_time)
            else:
                # Обновляем текущую 1M свечу
                current_1m_bar.high = max(current_1m_bar.high, price)
                current_1m_bar.low = min(current_1m_bar.low, price)
                current_1m_bar.close = price
                current_1m_bar.volume += quantity
            
            return closed_timeframes

        except Exception as e:
            self.logger.error(f"Ошибка при обработке тика: {e}")
            self.logger.debug(f"Данные тика: {trade_data}")
            return []

    def _update_aggregated_bars(self, last_closed_1m_bar: BarData) -> list[str]:
        """Обновляет старшие ТФ на основе закрытой минутной свечи."""
        closed_timeframes = []
        for tf_str in self.timeframes:
            if tf_str == '1M': continue
            tf_minutes = int(tf_str[:-1])
            
            current_agg_bar = self.current_bars.get(tf_str)
            agg_start_time = last_closed_1m_bar.start_time - timedelta(minutes=last_closed_1m_bar.start_time.minute % tf_minutes)

            if not current_agg_bar:
                self.current_bars[tf_str] = BarData(last_closed_1m_bar.open, last_closed_1m_bar.high, last_closed_1m_bar.low, last_closed_1m_bar.close, last_closed_1m_bar.volume, agg_start_time)
                continue

            if agg_start_time > current_agg_bar.start_time:
                self.bars[tf_str].append(current_agg_bar)
                closed_timeframes.append(tf_str)
                self.current_bars[tf_str] = BarData(last_closed_1m_bar.open, last_closed_1m_bar.high, last_closed_1m_bar.low, last_closed_1m_bar.close, last_closed_1m_bar.volume, agg_start_time)
            else:
                current_agg_bar.high = max(current_agg_bar.high, last_closed_1m_bar.high)
                current_agg_bar.low = min(current_agg_bar.low, last_closed_1m_bar.low)
                current_agg_bar.close = last_closed_1m_bar.close
                current_agg_bar.volume += last_closed_1m_bar.volume
        return closed_timeframes
