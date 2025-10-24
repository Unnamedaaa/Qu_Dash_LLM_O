# -*- coding: utf-8 -*-
import logging
import requests
import json
import prompts
from schemas import AISignal
from pydantic import ValidationError

class AI_Analyst:
    """Класс для взаимодействия с LLM через Ollama."""
    def __init__(self, host, port, model):
        self.base_url = f"{host}:{port}"
        self.api_url = f"{self.base_url}/api/chat"
        self.model = model
        self.logger = logging.getLogger('AI_Analyst')

    def _send_request(self, prompt, format=None):
        """Отправляет запрос к модели и возвращает ответ."""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False
        }
        if format:
            payload['format'] = format
        try:
            response = requests.post(self.api_url, json=payload, timeout=120)
            response.raise_for_status()
            response_data = response.json()
            return response_data.get('message', {}).get('content', '').strip()
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Ошибка запроса к Ollama: {e}")
            return None

    def get_initial_focus(self, market_data):
        """Шаг 1: Получает от ИИ основное направление для анализа."""
        prompt = prompts.STEP_1_PROMPT_TEMPLATE.format(**market_data)
        self.logger.info("Запрос к ИИ для определения фокуса...")
        focus = self._send_request(prompt)
        self.logger.info(f"ИИ определил фокус: '{focus}'")
        return focus

    def get_detailed_recommendation(self, focus, context_data):
        """Шаг 2: Получает детальную рекомендацию в формате JSON."""
        prompt = prompts.STEP_2_PROMPT_TEMPLATE.format(focus=focus, **context_data)
        
        self.logger.info("Запрос к ИИ для получения детальной рекомендации...")
        response_str = self._send_request(prompt, format='json')

        if not response_str:
            return None

        # Пытаемся извлечь и валидировать JSON из ответа модели
        try:
            recommendation_data = json.loads(response_str)
            
            # Валидация через Pydantic
            validated_signal = AISignal(**recommendation_data)
            self.logger.info(f"Получен и валидирован сигнал от ИИ: {validated_signal.model_dump_json(indent=2)}")
            return validated_signal

        except json.JSONDecodeError as e:
            self.logger.error(f"Не удалось распарсить JSON из ответа ИИ: {e}")
            self.logger.debug(f"Полный ответ ИИ: {response_str}")
            return None
        except ValidationError as e:
            self.logger.error(f"Ошибка валидации данных от ИИ: {e}")
            self.logger.debug(f"Полный ответ ИИ: {response_str}")
            return None
