import torch
import numpy as np
from typing import List, Dict, Any, Optional
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import logging
import asyncio
from datetime import datetime
import re

from src.core.config import settings

logger = logging.getLogger(__name__)


class SemanticSearchService:
    """Сервис семантического поиска для товаров"""

    def __init__(self, model_name: str = None):
        self.logger = logger

        if model_name is None:
            model_name = getattr(settings, 'semantic_model', 'intfloat/multilingual-e5-base')

        self.logger.info(f"Инициализация семантического поиска с моделью: {model_name}")

        try:
            self.model = SentenceTransformer(model_name)
            self.model.max_seq_length = 512
            self.logger.info("Модель успешно загружена")
        except Exception as e:
            self.logger.error(f"Ошибка загрузки модели: {e}")
            raise

        # Параметры
        self.batch_size = getattr(settings, 'semantic_batch_size', 64)
        self.similarity_threshold = getattr(settings, 'semantic_threshold', 0.35)

    def create_tender_text(self, tender_item: Dict[str, Any]) -> str:
        """Создать текстовое представление позиции тендера"""

        parts = []

        # Название товара
        name = tender_item.get('name')
        if name is not None:
            name = str(name).strip()
            if name:
                parts.append(f"Товар: {name}")

        # OKPD2 название для контекста
        okpd2_name = tender_item.get('okpd2Name')
        if okpd2_name is not None:
            okpd2_name = str(okpd2_name).strip()
            if okpd2_name and okpd2_name != name:
                parts.append(f"Категория: {okpd2_name}")

        # Добавляем обязательные характеристики
        characteristics = tender_item.get('characteristics', [])
        required_chars = [c for c in characteristics if c.get('required', False)]

        for char in required_chars[:5]:  # Максимум 5 важных характеристик
            char_name = char.get('name')
            if char_name is not None:
                char_name = str(char_name).strip()
            else:
                char_name = ''

            char_value = char.get('value')
            if char_value is not None:
                char_value = self._clean_value(str(char_value))
            else:
                char_value = ''

            if char_name and char_value:
                parts.append(f"{char_name}: {char_value}")

        result = ". ".join(parts)
        self.logger.debug(f"Текст тендера для эмбеддинга: '{result[:100]}...'")

        return result

    def create_product_text(self, product: Dict[str, Any]) -> str:
        """Создать текстовое представление товара из БД"""

        parts = []

        # Название товара
        title = product.get('sample_title')
        if title is not None:
            title = str(title).strip()
            if title:
                parts.append(f"Товар: {title}")

        # OKPD2 название
        okpd2_name = product.get('okpd2_name')
        if okpd2_name is not None:
            okpd2_name = str(okpd2_name).strip()
            if okpd2_name:
                parts.append(f"Категория: {okpd2_name}")

        # Бренд
        brand = product.get('sample_brand')
        if brand is not None:
            brand = str(brand).strip()
            if brand:
                parts.append(f"Бренд: {brand}")

        # Стандартизированные атрибуты
        std_attrs = product.get('standardized_attributes', [])
        for attr in std_attrs[:5]:  # Максимум 5 атрибутов
            attr_name = attr.get('standard_name')
            if attr_name is not None:
                attr_name = str(attr_name).strip()
            else:
                attr_name = ''

            attr_value = attr.get('standard_value')
            if attr_value is not None:
                attr_value = self._clean_value(str(attr_value))
            else:
                attr_value = ''

            if attr_name and attr_value:
                parts.append(f"{attr_name}: {attr_value}")

        # Важные нестандартизированные атрибуты
        non_std_attrs = product.get('non_standardized_attributes', [])
        important_attrs = ['тип', 'материал', 'модель', 'серия']

        for attr in non_std_attrs[:3]:
            attr_name = attr.get('original_name')
            if attr_name is None:
                continue
            attr_name = str(attr_name).strip()

            if any(imp in attr_name.lower() for imp in important_attrs):
                attr_value = attr.get('original_value')
                if attr_value is not None:
                    attr_value = self._clean_value(str(attr_value))
                    if attr_value:
                        parts.append(f"{attr_name}: {attr_value}")

        return ". ".join(parts)

    def _clean_value(self, value: str) -> str:
        """Очистить значение от операторов и лишних символов"""

        if not value:
            return ""

        # Убираем операторы сравнения
        value = re.sub(r'[≥≤<>]=?', '', value)

        # Убираем единицы измерения в конце
        value = re.sub(r'\s+(шт|мм|см|м|кг|г|л|мл)\.?$', '', value, flags=re.IGNORECASE)

        # Нормализуем пробелы
        value = ' '.join(value.split())

        return value.strip()

    async def compute_similarities(self, tender_item: Dict[str, Any],
                                   products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Вычислить семантическую схожесть асинхронно"""

        if not products:
            return []

        start_time = datetime.utcnow()
        self.logger.info(f"Вычисление семантической схожести для {len(products)} товаров")

        # Создаем текстовое представление тендера
        tender_text = self.create_tender_text(tender_item)

        # Создаем тексты товаров
        product_texts = []
        valid_products = []

        for product in products:
            try:
                text = self.create_product_text(product)
                if text:
                    product_texts.append(text)
                    valid_products.append(product)
            except Exception as e:
                self.logger.error(f"Ошибка создания текста для товара: {e}")
                continue

        if not product_texts:
            self.logger.warning("Не удалось создать тексты товаров")
            return products

        # Вычисляем эмбеддинги в батчах
        try:
            # Эмбеддинг тендера
            tender_embedding = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.model.encode([tender_text], convert_to_numpy=True)
            )

            # Эмбеддинги товаров батчами
            product_embeddings = []

            for i in range(0, len(product_texts), self.batch_size):
                batch = product_texts[i:i + self.batch_size]

                batch_embeddings = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda b=batch: self.model.encode(b, convert_to_numpy=True)
                )

                product_embeddings.append(batch_embeddings)

            # Объединяем все эмбеддинги
            if product_embeddings:
                product_embeddings = np.vstack(product_embeddings)
            else:
                return products

            # Вычисляем косинусную схожесть
            similarities = cosine_similarity(tender_embedding, product_embeddings)[0]

            # Добавляем скоры к товарам
            for product, similarity in zip(valid_products, similarities):
                product['semantic_score'] = float(similarity)

            # Добавляем нулевой скор товарам без текста
            for product in products:
                if 'semantic_score' not in product:
                    product['semantic_score'] = 0.0

            duration = (datetime.utcnow() - start_time).total_seconds()
            self.logger.info(f"Семантический поиск завершен за {duration:.2f} сек")

            # Статистика
            high_similarity = sum(1 for p in products if p.get('semantic_score', 0) > 0.7)
            medium_similarity = sum(1 for p in products if 0.5 <= p.get('semantic_score', 0) <= 0.7)
            low_similarity = sum(1 for p in products if p.get('semantic_score', 0) < 0.5)

            self.logger.debug(f"Распределение схожести: высокая={high_similarity}, "
                              f"средняя={medium_similarity}, низкая={low_similarity}")

        except Exception as e:
            self.logger.error(f"Ошибка вычисления семантической схожести: {e}")
            # В случае ошибки возвращаем товары без семантических скоров
            for product in products:
                if 'semantic_score' not in product:
                    product['semantic_score'] = 0.5  # Нейтральный скор

        return products

    def filter_by_similarity(self, products: List[Dict[str, Any]],
                             threshold: Optional[float] = None,
                             top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """Отфильтровать товары по семантической схожести"""

        if threshold is None:
            threshold = self.similarity_threshold

        # Фильтруем по порогу
        filtered = [p for p in products if p.get('semantic_score', 0) >= threshold]

        # Сортируем по семантической схожести
        filtered.sort(key=lambda x: x.get('semantic_score', 0), reverse=True)

        # Ограничиваем количество если указано
        if top_k and top_k > 0:
            filtered = filtered[:top_k]

        self.logger.info(f"После семантической фильтрации: {len(filtered)} из {len(products)} "
                         f"(порог: {threshold})")

        return filtered

    def combine_scores(self, products: List[Dict[str, Any]],
                       text_score_weight: float = 0.4,
                       semantic_score_weight: float = 0.6) -> List[Dict[str, Any]]:
        """Комбинировать текстовый и семантический скоры"""

        for product in products:
            # Получаем скоры
            text_score = product.get('text_search_score', 0.0)
            semantic_score = product.get('semantic_score', 0.0)

            # Нормализуем текстовый скор (если он большой)
            normalized_text_score = min(text_score / 10.0, 1.0) if text_score > 1 else text_score

            # Адаптивная формула
            if normalized_text_score < 0.1 and semantic_score > 0.7:
                # Подозрительно: низкий текстовый, высокий семантический
                combined_score = 0.7 * normalized_text_score + 0.3 * semantic_score
            else:
                # Стандартная формула
                combined_score = (
                        text_score_weight * normalized_text_score +
                        semantic_score_weight * semantic_score
                )

            product['combined_score'] = combined_score
            product['normalized_text_score'] = normalized_text_score

        # Сортируем по комбинированному скору
        products.sort(key=lambda x: x.get('combined_score', 0), reverse=True)

        return products