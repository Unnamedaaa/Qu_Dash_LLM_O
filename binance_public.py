# -*- coding: utf-8 -*-
"""Модуль для получения публичных рыночных данных с Binance."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests


class BinanceAPIError(Exception):
    """Ошибка взаимодействия с публичным API Binance."""


class BinancePublicData:
    """Клиент для работы с публичными REST endpoints Binance."""

    BASE_URL = 'https://api.binance.com/api/v3'

    def __init__(self, session: Optional[requests.Session] = None, timeout: float = 10.0):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.logger = logging.getLogger(self.__class__.__name__)

    def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f'{self.BASE_URL}{endpoint}'
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
        except requests.HTTPError as exc:
            message = self._build_error_message(response)
            self.logger.error(message)
            raise BinanceAPIError(message) from exc
        except requests.RequestException as exc:
            message = f'Сетевая ошибка при обращении к {url}: {exc}'
            self.logger.error(message)
            raise BinanceAPIError(message) from exc

        try:
            return response.json()
        except ValueError as exc:
            message = f'Некорректный JSON ответ от {url}: {response.text}'
            self.logger.error(message)
            raise BinanceAPIError(message) from exc

    def _build_error_message(self, response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            payload = response.text

        return (
            f'Binance API вернул статус {response.status_code} для {response.url}. '
            f'Ответ: {payload}'
        )

    def get_ticker_price(self, symbol: str) -> Dict[str, float]:
        """Возвращает текущую цену инструмента."""
        params = {'symbol': symbol.upper()}
        data = self._request('/ticker/price', params)
        return {'symbol': data['symbol'], 'price': float(data['price'])}

    def get_klines(
        self,
        symbol: str,
        interval: str = '1m',
        limit: int = 500,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Возвращает исторические свечи (OHLCV) для инструмента."""
        params: Dict[str, Any] = {'symbol': symbol.upper(), 'interval': interval, 'limit': limit}
        if start_time is not None:
            params['startTime'] = start_time
        if end_time is not None:
            params['endTime'] = end_time

        raw_klines = self._request('/klines', params)
        klines: List[Dict[str, Any]] = []
        for item in raw_klines:
            klines.append(
                {
                    'open_time': int(item[0]),
                    'open': float(item[1]),
                    'high': float(item[2]),
                    'low': float(item[3]),
                    'close': float(item[4]),
                    'volume': float(item[5]),
                    'close_time': int(item[6]),
                    'quote_asset_volume': float(item[7]),
                    'number_of_trades': int(item[8]),
                    'taker_buy_base_volume': float(item[9]),
                    'taker_buy_quote_volume': float(item[10]),
                }
            )
        return klines

    def get_order_book(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """Возвращает текущий стакан заявок (bids/asks)."""
        params: Dict[str, Any] = {'symbol': symbol.upper(), 'limit': limit}
        data = self._request('/depth', params)
        return {
            'lastUpdateId': data['lastUpdateId'],
            'bids': [
                {'price': float(entry[0]), 'quantity': float(entry[1])}
                for entry in data.get('bids', [])
            ],
            'asks': [
                {'price': float(entry[0]), 'quantity': float(entry[1])}
                for entry in data.get('asks', [])
            ],
        }

    def get_recent_trades(self, symbol: str, limit: int = 500) -> List[Dict[str, Any]]:
        """Возвращает список последних сделок."""
        params: Dict[str, Any] = {'symbol': symbol.upper(), 'limit': limit}
        raw_trades = self._request('/trades', params)
        trades: List[Dict[str, Any]] = []
        for trade in raw_trades:
            trades.append(
                {
                    'id': trade['id'],
                    'price': float(trade['price']),
                    'quantity': float(trade['qty']),
                    'quote_quantity': float(trade['quoteQty']),
                    'time': int(trade['time']),
                    'is_buyer_maker': trade['isBuyerMaker'],
                    'is_best_match': trade['isBestMatch'],
                }
            )
        return trades


def example_usage() -> None:
    """Простой пример использования клиента BinancePublicData."""
    client = BinancePublicData()
    symbol = 'BTCUSDT'

    ticker = client.get_ticker_price(symbol)
    print(f"Текущая цена {ticker['symbol']}: {ticker['price']}")

    klines = client.get_klines(symbol, interval='1h', limit=3)
    print('Последние 3 часовые свечи:')
    for candle in klines:
        open_time = datetime.utcfromtimestamp(candle['open_time'] / 1000).strftime('%Y-%m-%d %H:%M')
        print(
            f"  {open_time} | O: {candle['open']} H: {candle['high']} L: {candle['low']} "
            f"C: {candle['close']} V: {candle['volume']}"
        )

    order_book = client.get_order_book(symbol, limit=5)
    best_bid = order_book['bids'][0] if order_book['bids'] else None
    best_ask = order_book['asks'][0] if order_book['asks'] else None
    print('Топ уровня стакана:')
    if best_bid:
        print(f"  Лучшая покупка: {best_bid['quantity']} @ {best_bid['price']}")
    if best_ask:
        print(f"  Лучшая продажа: {best_ask['quantity']} @ {best_ask['price']}")

    trades = client.get_recent_trades(symbol, limit=5)
    print('Последние сделки:')
    for trade in trades:
        side = 'sell' if trade['is_buyer_maker'] else 'buy'
        time_str = datetime.utcfromtimestamp(trade['time'] / 1000).strftime('%H:%M:%S')
        print(f"  {time_str} | {side.upper()} {trade['quantity']} @ {trade['price']}")


if __name__ == '__main__':
    example_usage()
