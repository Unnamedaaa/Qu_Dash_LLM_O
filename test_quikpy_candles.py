import sys
import os
import logging
from QuikPy import QuikPy

# Добавляем путь к корневой папке проекта, чтобы импортировать config
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('TestQuikPyCandles')

def test_get_candles():
    logger.info("Запуск теста get_candles_from_data_source...")
    qp = QuikPy(host=config.QUIK_HOST)

    if not qp.is_connected()['data'] == 1:
        logger.error("Не удалось подключиться к QUIK. Убедитесь, что QUIK запущен и QuikPy настроен правильно.")
        qp.close_connection_and_thread()
        return
    logger.info("Успешно подключено к QUIK.")

    class_code = config.CLASS_CODE
    sec_code = config.SEC_CODE
    timeframe = '1M' # Тестируем 1M таймфрейм, как в initial_backfill
    count = 1000

    interval_map = {
        '1M': 1,
        '5M': 5,
        '15M': 15,
        '30M': 30,
        '240M': 240 # 4H
    }
    interval = interval_map.get(timeframe)

    if not interval:
        logger.error(f"Неподдерживаемый таймфрейм: {timeframe}")
        qp.close_connection_and_thread()
        return

    logger.info(f"Запрос свечей для {class_code}.{sec_code}, таймфрейм: {timeframe} (интервал: {interval}), количество: {count}")
    try:
        candles = qp.get_candles_from_data_source(class_code, sec_code, interval, count=count)
        logger.info(f"Получен ответ от QuikPy: {candles}")

        if candles and candles.get('data'):
            if isinstance(candles['data'], list):
                logger.info(f"Успешно получено {len(candles['data'])} свечей.")
                # Выводим первые 5 свечей для примера
                for i, bar in enumerate(candles['data'][:5]):
                    logger.info(f"Свеча {i+1}: {bar}")
            else:
                logger.error(f"QuikPy вернул данные, но они не являются списком: {candles['data']}")
        else:
            logger.warning("QuikPy не вернул данные или вернул пустой ответ.")

    except Exception as e:
        logger.error(f"Произошла ошибка при запросе свечей: {e}")
    finally:
        qp.close_connection_and_thread()
        logger.info("Соединение с QUIK закрыто.")

if __name__ == "__main__":
    test_get_candles()
