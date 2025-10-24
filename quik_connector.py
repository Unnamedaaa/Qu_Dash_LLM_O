# -*- coding: utf-8 -*-
import logging
from QuikPy import QuikPy

class QuikConnector:
    """Класс для инкапсуляции работы с QuikPy."""
    def __init__(self, host):
        self.qp = QuikPy(host=host)
        self.logger = logging.getLogger('QuikConnector')

    def is_connected(self):
        """Проверка соединения с терминалом QUIK."""
        try:
            return self.qp.is_connected()['data'] == 1
        except Exception as e:
            self.logger.error(f"Ошибка проверки соединения с QUIK: {e}")
            return False

    def get_candles(self, class_code, sec_code, timeframe, count=0):
        """Получение свечей для инструмента. По умолчанию все."""
        interval_map = {
            '1M': 1,
            '5M': 5,
            '15M': 15,
            '30M': 30,
            '240M': 240 # 4H
        }
        interval = interval_map.get(timeframe)
        if not interval:
            self.logger.error(f"Неподдерживаемый таймфрейм: {timeframe}")
            return None

        try:
            # param='-' в QuikPy.py, но мы хотим передавать пустую строку, если не указан
            param_to_pass = '' # По умолчанию пустая строка
            # Если в будущем понадобится передавать param, то можно добавить его в сигнатуру get_candles
            candles = self.qp.get_candles_from_data_source(class_code, sec_code, interval)
            if candles and candles.get('data'):
                if isinstance(candles['data'], list):
                    return candles['data']
                else:
                    self.logger.error(f"QUIK вернул ошибку вместо списка баров: {candles['data']}")
                    return None
            self.logger.warning(f"Не получены свечи для {class_code}.{sec_code} с интервалом {timeframe}")
            return None
        except Exception as e:
            self.logger.error(f"Ошибка получения свечей: {e}", exc_info=True)
            return None

    def subscribe_to_order_book(self, class_code, sec_code):
        """Подписка на получение стакана."""
        self.logger.info(f"Подписка на стакан для {class_code}.{sec_code}")
        self.qp.subscribe_level2_quotes(class_code, sec_code)

    def subscribe_to_candles(self, class_code, sec_code, timeframe):
        """Подписка на получение новых свечей."""
        interval_map = {
            '1M': 1,
            '15M': 15,
            '240M': 240
        }
        interval = interval_map.get(timeframe)
        if not interval:
            self.logger.error(f"Неподдерживаемый таймфрейм для подписки: {timeframe}")
            return
        
        self.logger.info(f"Подписка на свечи {timeframe} для {class_code}.{sec_code}")
        self.qp.subscribe_to_candles(class_code, sec_code, interval)

    def subscribe_to_all_trades(self, class_code, sec_code):
        """Подписка на получение всех сделок (лента)."""
        self.logger.info(f"Подписка на ленту сделок для {class_code}.{sec_code}")
        self.qp.param_request(class_code, sec_code, 'ALL_TRADES')

    def get_order_book(self, class_code, sec_code):
        """Получение стакана по инструменту."""
        return self.qp.get_quote_level2(class_code, sec_code)

    def close(self):
        """Закрытие соединения."""
        self.qp.close_connection_and_thread()
        self.logger.info("Соединение с QUIK закрыто.")