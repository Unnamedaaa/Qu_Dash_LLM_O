# -*- coding: utf-8 -*-
import sys
import os
import logging
import time
import json
import traceback
from datetime import datetime
from threading import Event, Lock
from enum import Enum

import pandas as pd

# -- Импорты из проекта --
import config
from quik_connector import QuikConnector
from ai_analyst import AI_Analyst
from data_handler import DataHandler
from technical_analysis import get_trend_and_volatility, calculate_order
from schemas import AISignal, TradeOrder
from market_profile import calculate_volume_profile
from order_flow import OrderFlowAnalyzer
from dashboard_utils import render_text_chart

# -- Импорты для дашборда --
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.live import Live
from rich.table import Table
from rich.text import Text

# --- Глобальные переменные и перечисления ---
shutdown_event = Event()

class BotStatus(Enum):
    """Перечисление состояний бота."""
    INITIALIZING = ("Инициализация", "yellow")
    WAITING_FOR_SIGNAL = ("Ожидание сигнала", "cyan")
    ANALYZING = ("Анализ...", "magenta")
    WAITING_FOR_ENTRY = ("Ожидание точки входа", "yellow")
    POSITION_OPEN = ("Позиция открыта", "green")
    ERROR = ("Ошибка", "red")

    def __init__(self, description, color):
        self.description = description
        self.color = color

def setup_trade_journal():
    """Создает CSV файл для журнала сделок, если он не существует."""
    if not os.path.exists('trade_journal.csv'):
        with open('trade_journal.csv', 'w', encoding='utf-8') as f:
            f.write("entry_time,exit_time,signal,entry_price,exit_price,sl,tp,result,profit_r\n")

# --- Основной класс бота ---
class TradingBot:
    def __init__(self):
        self.console = Console()
        self.dashboard = self._create_layout()
        self.bot_state = BotStatus.INITIALIZING
        self.state_lock = Lock()
        self.last_log_messages = []
        self.market_state_content = "Ожидание данных..."
        self.ai_focus_content = "Ожидание анализа..."
        self.trade_order_content = "Нет активных ордеров."
        self.sparkline_data = []
        self.hot_clusters = []
        self.active_trade: TradeOrder | None = None
        self.last_trade_result: str | None = None

        # Инициализация компонентов
        self.quik = QuikConnector(config.QUIK_HOST)
        self.analyst = AI_Analyst(config.OLLAMA_HOST, config.OLLAMA_PORT, config.MODEL_NAME)
        self.data_handler = DataHandler(self.quik, config.CLASS_CODE, config.SEC_CODE, config.TIMEFRAMES)
        self.order_flow_analyzer = OrderFlowAnalyzer(config.ANALYSIS_CONFIG)

    def _create_layout(self) -> "Layout":
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(ratio=1, name="main"),
            Layout(size=10, name="footer"),
        )
        layout["main"].split_row(Layout(name="left"), Layout(name="right"))
        layout["left"].split_column(Layout(name="market_state"), Layout(name="ai_focus"))
        layout["right"].split_column(Layout(name="trade_order"), Layout(name="clusters"), Layout(name="icebergs"), Layout(name="chart"))
        layout["footer"].update(Panel("", title="[bold blue]Лог событий[/bold blue]", border_style="blue"))
        return layout

    def _update_dashboard(self):
        self.dashboard["footer"].update(Panel("\n".join(self.last_log_messages), title="[bold blue]Лог событий[/bold blue]", border_style="blue"))
        status_text = Text(f"AI Trading Bot v4.0 | Состояние: {self.bot_state.description}", justify="center", style=f"bold {self.bot_state.color}")
        self.dashboard["header"].update(Panel(status_text, border_style=self.bot_state.color))
        self.dashboard["market_state"].update(Panel(self.market_state_content, title="[bold green]Состояние Рынка[/bold green]", border_style="green"))
        self.dashboard["ai_focus"].update(Panel(self.ai_focus_content, title="[bold yellow]Фокус AI[/bold yellow]", border_style="yellow"))

        # Обновление панели ордера/позиции
        if self.bot_state == BotStatus.POSITION_OPEN and self.active_trade:
            df_1m = self.data_handler.get_bars_as_dataframe('1M')
            if not df_1m.empty:
                last_price = df_1m['close'].iloc[-1]
                pnl = (last_price - self.active_trade.entry_price) if self.active_trade.signal == 'LONG' else (self.active_trade.entry_price - last_price)
                pnl_color = "green" if pnl >= 0 else "red"
                risk_per_point = abs(self.active_trade.entry_price - self.active_trade.stop_loss)
                pnl_r = pnl / risk_per_point if risk_per_point > 0 else 0

                pos_table = Table(show_header=False, box=None)
                pos_table.add_row("[bold]Сигнал[/bold]", f"[bold {'green' if self.active_trade.signal == 'LONG' else 'red'}]{self.active_trade.signal}[/]")
                pos_table.add_row("[bold]Цена входа[/bold]", f"{self.active_trade.entry_price:.2f}")
                pos_table.add_row("[bold]SL/TP[/bold]", f"{self.active_trade.stop_loss:.2f} / {self.active_trade.take_profit:.2f}")
                pos_table.add_row(f"[bold]P/L[/bold]", f"[{pnl_color}]{pnl:.2f} ({pnl_r:.2f}R)[/{pnl_color}]")
                self.trade_order_content = pos_table
        elif self.last_trade_result:
             self.trade_order_content = f"Результат последней сделки: {self.last_trade_result}"
        elif self.bot_state == BotStatus.WAITING_FOR_ENTRY and self.active_trade:
            self.trade_order_content = f"Ожидание входа в {self.active_trade.signal}...\nSL: {self.active_trade.stop_loss:.2f}, TP: {self.active_trade.take_profit:.2f}"
        else:
            self.trade_order_content = "Нет активных ордеров."
        self.dashboard["trade_order"].update(Panel(self.trade_order_content, title="[bold magenta]Позиция[/bold magenta]", border_style="magenta"))

        # Остальные панели
        if self.hot_clusters:
            cluster_table = Table(show_header=True, header_style="bold cyan")
            cluster_table.add_column("Цена", justify="right"); cluster_table.add_column("Объем", justify="right")
            for cluster in self.hot_clusters[:4]: cluster_table.add_row(f"{cluster['price']:.2f}", str(cluster['volume']))
            self.dashboard["clusters"].update(Panel(cluster_table, title="[bold cyan]Кластеры Объема[/bold cyan]", border_style="cyan"))
        else:
            self.dashboard["clusters"].update(Panel("Нет", title="[bold cyan]Кластеры Объема[/bold cyan]", border_style="cyan"))
        
        iceberg_events = self.order_flow_analyzer.absorption_events
        if iceberg_events: self.dashboard["icebergs"].update(Panel("\n".join(iceberg_events), title="[bold red]Поглощения[/bold red]", border_style="red"))
        else: self.dashboard["icebergs"].update(Panel("Нет", title="[bold red]Поглощения[/bold red]", border_style="red"))
        
        if self.sparkline_data: self.dashboard["chart"].update(Panel(render_text_chart(self.sparkline_data, width=50, height=8), title="[bold cyan]График (1M)[/bold cyan]", border_style="cyan"))
        else: self.dashboard["chart"].update(Panel("Нет данных для графика", title="[bold cyan]График (1M)[/bold cyan]", border_style="cyan"))

    def log(self, message: str):
        timestamp = datetime.now().strftime('%H:%M:%S')
        logging.info(message)
        self.last_log_messages.append(f"[{timestamp}] {message}")
        if len(self.last_log_messages) > 8: self.last_log_messages.pop(0)

    def set_state(self, new_state: BotStatus):
        with self.state_lock:
            if self.bot_state != new_state:
                self.bot_state = new_state
                self.log(f"Смена состояния -> {new_state.description}")

    def on_new_trade(self, trade: dict):
        trade_data = trade['data']
        self.order_flow_analyzer.process_trade(trade_data)
        closed_tfs = self.data_handler.process_new_trade(trade_data)

        with self.state_lock:
            if self.bot_state == BotStatus.WAITING_FOR_ENTRY:
                self.find_entry_point(float(trade_data['price']))
            elif self.bot_state == BotStatus.POSITION_OPEN:
                self.manage_open_position(float(trade_data['price']))

        if closed_tfs:
            self.log(f"Закрыты свечи: {', '.join(closed_tfs)}")
            self.sparkline_data = self.data_handler.get_bars_as_dataframe('1M')['close'].tail(config.UI_SETTINGS['chart_bar_count']).tolist()
            if config.PRIMARY_TIMEFRAME in closed_tfs and self.bot_state == BotStatus.WAITING_FOR_SIGNAL:
                self.analyze_and_act()

    def on_quote_update(self, quote: dict):
        quote_data = quote.get('data', {})
        bids = quote_data.get('bids', [])
        offers = quote_data.get('offers', [])
        if bids and offers:
            self.order_flow_analyzer.process_order_book(bids, offers)

    def find_entry_point(self, current_price: float):
        if not self.active_trade: return
        self.active_trade.entry_price = current_price
        self.log(f"[green]ВХОД В ПОЗИЦИЮ[/green] {self.active_trade.signal} по цене {current_price:.2f}")
        self.set_state(BotStatus.POSITION_OPEN)

    def manage_open_position(self, current_price: float):
        if not self.active_trade: return
        trade = self.active_trade
        result = None
        exit_price = current_price
        if trade.signal == 'LONG':
            if current_price <= trade.stop_loss: result = "SL_HIT"
            elif current_price >= trade.take_profit: result = "TP_HIT"
        elif trade.signal == 'SHORT':
            if current_price >= trade.stop_loss: result = "SL_HIT"
            elif current_price <= trade.take_profit: result = "TP_HIT"
        
        if result:
            pnl_r = config.RISK_REWARD_RATIO if result == "TP_HIT" else -1.0
            self.last_trade_result = f"[{'green' if pnl_r > 0 else 'red'}]{result} ({pnl_r:.2f}R)[/]"
            self.log(f"[red]ПОЗИЦИЯ ЗАКРЫТА[/red] по {result} @ {exit_price:.2f}")
            with open('trade_journal.csv', 'a', encoding='utf-8') as f:
                f.write(f"{datetime.now()},{datetime.now()},{trade.signal},{trade.entry_price},{exit_price},{trade.stop_loss},{trade.take_profit},{result},{pnl_r}\n")
            self.active_trade = None
            self.set_state(BotStatus.WAITING_FOR_SIGNAL)

    def _prepare_market_summary(self):
        try:
            df_15m = self.data_handler.get_bars_as_dataframe("15M")
            df_240m = self.data_handler.get_bars_as_dataframe("240M")
            if df_15m.empty or df_240m.empty:
                self.log("Недостаточно данных для анализа.")
                return None, None

            trend_4h, _, _, _, _, _ = get_trend_and_volatility(df_240m, config.INDICATOR_SETTINGS)
            trend_15m, vol_15m, _, rsi_15m, macd_15m, macd_signal_15m = get_trend_and_volatility(df_15m, config.INDICATOR_SETTINGS)
            poc, vah, val = calculate_volume_profile(df_15m, settings=config.INDICATOR_SETTINGS)
            cvd_divergence = self.order_flow_analyzer.check_divergence()

            market_summary = {
                "instrument": f"{config.CLASS_CODE}.{config.SEC_CODE}",
                "trend_4h": trend_4h,
                "trend_15m": trend_15m,
                "volatility_15m": vol_15m,
                "rsi_15m": f"{rsi_15m:.2f}" if rsi_15m is not None else "Н/Д",
                "cvd_divergence": cvd_divergence,
                "poc": f"{poc:.2f}" if poc is not None else "Н/Д",
                "vah": f"{vah:.2f}" if vah is not None else "Н/Д",
                "val": f"{val:.2f}" if val is not None else "Н/Д",
            }
            
            table = Table(show_header=False, box=None, padding=(0, 1))
            for key, value in market_summary.items():
                table.add_row(f"[bold]{key}[/bold]", str(value))
            self.market_state_content = table

            focus_context = {**market_summary, "macd_15m": f"MACD: {macd_15m:.2f}, Signal: {macd_signal_15m:.2f}" if macd_15m is not None else "Н/Д"}
            return market_summary, focus_context, (poc, vah, val)
        except (KeyError, IndexError) as e:
            self.log(f"[red]Ошибка обработки данных для рыночной сводки: {e}[/red]")
            logging.error(traceback.format_exc())
            return None, None

    def _get_ai_focus(self, focus_context):
        try:
            focus = self.analyst.get_initial_focus(focus_context)
            if not focus or "Оставаться вне рынка" in focus:
                self.log("AI: Остаемся вне рынка.")
                self.ai_focus_content = focus or "Н/Д"
                return None
            self.ai_focus_content = focus
            return focus
        except requests.exceptions.RequestException as e:
            self.log(f"[red]Ошибка сети при запросе фокуса у AI: {e}[/red]")
            logging.error(traceback.format_exc())
            return None

    def _get_detailed_ai_recommendation(self, focus, market_summary):
        try:
            order_book_raw = self.quik.get_order_book(config.CLASS_CODE, config.SEC_CODE)
            bids = order_book_raw.get('data', {}).get('bids', [])
            offers = order_book_raw.get('data', {}).get('offers', [])
            bid_vol = sum(b['quantity'] for b in bids[:5])
            offer_vol = sum(o['quantity'] for o in offers[:5])
            imbalance = (bid_vol - offer_vol) / (bid_vol + offer_vol) if (bid_vol + offer_vol) > 0 else 0
            self.hot_clusters = self.order_flow_analyzer.update_clusters()
            iceberg_events = list(self.order_flow_analyzer.absorption_events)
            
            context_data = {
                "order_book_imbalance_5_levels": f"{imbalance:.2%}",
                "cumulative_delta_last_50_ticks": list(self.order_flow_analyzer.cvd_history)[-50:],
                "hot_clusters_last_15_sec": self.hot_clusters,
                "absorption_events_last_minute": iceberg_events,
                "recent_bars_1m": [bar.to_dict() for bar in list(self.data_handler.bars["1M"])[-config.BOT_LOGIC_SETTINGS['ai_context_bar_count']:]],
                **market_summary
            }

            ai_signal = self.analyst.get_detailed_recommendation(focus, context_data)
            if not ai_signal or ai_signal.signal == 'WAIT':
                self.log("AI не дал четкого сигнала на вход.")
                return None
            return ai_signal
        except requests.exceptions.RequestException as e:
            self.log(f"[red]Ошибка сети при запросе рекомендации у AI: {e}[/red]")
            logging.error(traceback.format_exc())
            return None
        except Exception as e:
            self.log(f"[red]Непредвиденная ошибка при получении рекомендации: {e}[/red]")
            logging.error(traceback.format_exc())
            return None

    def analyze_and_act(self):
        self.set_state(BotStatus.ANALYZING)
        try:
            market_summary, focus_context, profile_levels = self._prepare_market_summary()
            if not market_summary:
                self.set_state(BotStatus.WAITING_FOR_SIGNAL)
                return

            focus = self._get_ai_focus(focus_context)
            if not focus:
                self.set_state(BotStatus.WAITING_FOR_SIGNAL)
                return

            ai_signal = self._get_detailed_ai_recommendation(focus, market_summary)
            if not ai_signal:
                self.set_state(BotStatus.WAITING_FOR_SIGNAL)
                return

            _, _, atr, _, _, _ = get_trend_and_volatility(self.data_handler.get_bars_as_dataframe(config.PRIMARY_TIMEFRAME), config.INDICATOR_SETTINGS)
            if atr is None or not atr.any():
                self.log("Не удалось рассчитать ATR для ордера.")
                self.set_state(BotStatus.WAITING_FOR_SIGNAL)
                return
            order_params = calculate_order(
                signal=ai_signal.signal, bars_df=self.data_handler.get_bars_as_dataframe(config.PRIMARY_TIMEFRAME),
                atr=atr[-1], profile_levels=profile_levels, atr_multiplier=config.ATR_SL_MULTIPLIER,
                risk_reward_ratio=config.RISK_REWARD_RATIO
            )
            if not order_params: 
                self.log("Не удалось рассчитать параметры ордера.")
                self.set_state(BotStatus.WAITING_FOR_SIGNAL)
                return
            
            entry_price, stop_loss, take_profit = order_params
            self.active_trade = TradeOrder(signal=ai_signal.signal, entry_price=0, stop_loss=stop_loss, take_profit=take_profit, confidence=ai_signal.confidence, justification=ai_signal.justification)
            self.log(f"AI дал сигнал {ai_signal.signal}. Переход в режим ожидания точки входа.")
            self.set_state(BotStatus.WAITING_FOR_ENTRY)
        except Exception as e:
            self.log(f"[red]КРИТИЧЕСКАЯ ОШИБКА в цикле анализа: {e}[/red]")
            logging.error(traceback.format_exc())
            self.set_state(BotStatus.ERROR)

    def run(self):
        setup_logging()
        setup_trade_journal()
        try:
            with Live(self.dashboard, screen=True, redirect_stderr=False, vertical_overflow="visible") as live:
                self.log("Запуск AI Trading Bot v4.0 (Тактический)... ")
                live.update(self.dashboard)
                if not self.quik.is_connected():
                    self.log("[red]Не удалось подключиться к QUIK.[/red]")
                    self.set_state(BotStatus.ERROR)
                    return
                self.log("[green]Успешно подключено к QUIK.[/green]")
                self.data_handler.initial_backfill()
                self.log(f"Исторические данные загружены. Свечей 1M: {len(self.data_handler.bars['1M'])}")
                self.sparkline_data = self.data_handler.get_bars_as_dataframe('1M')['close'].tail(config.UI_SETTINGS['chart_bar_count']).tolist()
                self.analyze_and_act() # Выполняем первичный анализ после загрузки истории
                self.quik.qp.on_all_trade = self.on_new_trade
                self.quik.qp.on_quote = self.on_quote_update
                self.quik.subscribe_to_all_trades(config.CLASS_CODE, config.SEC_CODE)
                self.log(f"Подписка на ленту сделок для {config.CLASS_CODE}.{config.SEC_CODE} оформлена.")
                self.quik.subscribe_to_order_book(config.CLASS_CODE, config.SEC_CODE)
                self.log(f"Подписка на стакан для {config.CLASS_CODE}.{config.SEC_CODE} оформлена.")
                if self.bot_state not in [BotStatus.WAITING_FOR_ENTRY, BotStatus.POSITION_OPEN]:
                    self.set_state(BotStatus.WAITING_FOR_SIGNAL)
                while not shutdown_event.is_set():
                    self._update_dashboard()
                    live.update(self.dashboard)
                    time.sleep(0.5)
        except KeyboardInterrupt:
            self.console.print("\n[bold yellow]Завершение работы...[/bold yellow]")
        except Exception as e:
            logging.critical(f"Неперехваченная ошибка: {e}", exc_info=True)
            self.console.print(f"\n[bold red]КРИТИЧЕСКАЯ ОШИБКА: {e}[/bold red]")
        finally:
            shutdown_event.set()
            if self.quik:
                self.quik.close()
            self.console.print("[bold]Бот остановлен.[/bold]")

def setup_logging():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', filename="trading_bot.log", filemode='w')

if __name__ == "__main__":
    bot = TradingBot()
    bot.run()