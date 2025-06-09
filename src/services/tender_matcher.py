import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import re
import asyncio
import time
from functools import lru_cache

from src.models.tender import (
    TenderRequest, TenderItem, TenderItemMatch,
    MatchedProduct, MatchedSupplier, TenderMatchingResult
)
from src.storage.unique_products_mongo import UniqueProductsMongoStore
from src.core.config import settings

logger = logging.getLogger(__name__)


class TenderMatchingService:
    """Сервис для сопоставления товаров из тендера с товарами из БД"""

    def __init__(self, unique_products_store: UniqueProductsMongoStore):
        self.unique_products_store = unique_products_store
        self._okpd2_cache = {} if settings.enable_okpd2_cache else None

    def parse_numeric_condition(self, value: str) -> Tuple[str, float, Optional[float]]:
        """
        Парсить числовое условие из строки
        Возвращает: (оператор, значение1, значение2_опционально)
        """
        value = value.strip()

        # Паттерны для разных условий
        patterns = [
            (r'^≥\s*(\d+(?:\.\d+)?)\s*и\s*<\s*(\d+(?:\.\d+)?)$', 'between_ge_lt'),
            (r'^>\s*(\d+(?:\.\d+)?)\s*и\s*≤\s*(\d+(?:\.\d+)?)$', 'between_gt_le'),
            (r'^≥\s*(\d+(?:\.\d+)?)\s*и\s*≤\s*(\d+(?:\.\d+)?)$', 'between_ge_le'),
            (r'^≥\s*(\d+(?:\.\d+)?)$', 'gte'),
            (r'^≤\s*(\d+(?:\.\d+)?)$', 'lte'),
            (r'^>\s*(\d+(?:\.\d+)?)$', 'gt'),
            (r'^<\s*(\d+(?:\.\d+)?)$', 'lt'),
            (r'^(\d+(?:\.\d+)?)$', 'eq'),
        ]

        for pattern, operator in patterns:
            match = re.match(pattern, value)
            if match:
                if operator.startswith('between'):
                    return operator, float(match.group(1)), float(match.group(2))
                else:
                    return operator, float(match.group(1)), None

        # Если не удалось распарсить - возвращаем как строку
        return 'string', value, None

    def check_numeric_match(self, tender_value: str, product_value: str) -> bool:
        """Проверить соответствие числовых значений"""
        tender_op, tender_val1, tender_val2 = self.parse_numeric_condition(tender_value)
        product_op, product_val1, product_val2 = self.parse_numeric_condition(product_value)

        # Если не удалось распарсить - сравниваем как строки
        if tender_op == 'string' or product_op == 'string':
            return tender_value.lower() == product_value.lower()

        # Проверяем соответствие условий
        if tender_op == 'eq':
            return product_val1 == tender_val1
        elif tender_op == 'gte':
            return product_val1 >= tender_val1
        elif tender_op == 'lte':
            return product_val1 <= tender_val1
        elif tender_op == 'gt':
            return product_val1 > tender_val1
        elif tender_op == 'lt':
            return product_val1 < tender_val1
        elif tender_op.startswith('between'):
            # Для диапазонов проверяем пересечение
            if product_op == 'eq':
                if tender_op == 'between_ge_lt':
                    return tender_val1 <= product_val1 < tender_val2
                elif tender_op == 'between_gt_le':
                    return tender_val1 < product_val1 <= tender_val2
                else:  # between_ge_le
                    return tender_val1 <= product_val1 <= tender_val2
            elif product_op == 'gte':
                return product_val1 >= tender_val1
            elif product_op == 'lte':
                return product_val1 <= tender_val2 if tender_val2 else tender_val1

        return False

    def calculate_match_score(
            self,
            tender_item: TenderItem,
            product: Dict[str, Any]
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Рассчитать степень соответствия товара требованиям тендера
        Возвращает: (score, match_details)
        """
        matched_attributes = []
        missing_attributes = []
        total_score = 0.0

        # 1. Проверяем OKPD2 код (обязательное условие)
        if not tender_item.okpd2Code or not product['okpd2_code'].startswith(tender_item.okpd2Code):
            return 0.0, {
                "matched": False,
                "reason": "OKPD2 code mismatch",
                "tender_okpd2": tender_item.okpd2Code,
                "product_okpd2": product['okpd2_code']
            }

        # Если нет характеристик у товара - базовое совпадение по OKPD2
        if not tender_item.characteristics:
            return 0.5, {  # Базовый score 50% только за совпадение OKPD2
                "matched_attributes": [],
                "missing_attributes": [],
                "total_required": 0,
                "total_matched": 0,
                "note": "No characteristics to match, OKPD2 match only"
            }

        # 2. Сопоставляем характеристики (БЕЗ НОРМАЛИЗАЦИИ - уже стандартизированы)
        tender_chars = {
            ch.name: ch  # Используем имя как есть
            for ch in tender_item.characteristics
        }

        product_attrs = {
            attr.get('standard_name', ''): attr
            for attr in product.get('standardized_attributes', [])
        }

        # Проверяем обязательные характеристики
        for char_name, tender_char in tender_chars.items():
            if not tender_char.required:
                continue

            if char_name in product_attrs:
                product_attr = product_attrs[char_name]

                # Сравниваем значения
                if tender_char.type == "Количественная":
                    if self.check_numeric_match(tender_char.value, str(product_attr.get('standard_value', ''))):
                        matched_attributes.append({
                            "name": char_name,
                            "tender_value": tender_char.value,
                            "product_value": product_attr.get('standard_value'),
                            "unit": tender_char.unit
                        })
                        total_score += 1.0
                    else:
                        missing_attributes.append({
                            "name": char_name,
                            "tender_value": tender_char.value,
                            "product_value": product_attr.get('standard_value'),
                            "reason": "value mismatch"
                        })
                else:  # Качественная
                    tender_val = str(tender_char.value).strip()
                    product_val = str(product_attr.get('standard_value', '')).strip()

                    if tender_val == product_val or "эквивалент" in tender_val.lower():
                        matched_attributes.append({
                            "name": char_name,
                            "tender_value": tender_char.value,
                            "product_value": product_attr.get('standard_value')
                        })
                        total_score += 1.0
                    else:
                        missing_attributes.append({
                            "name": char_name,
                            "tender_value": tender_char.value,
                            "product_value": product_attr.get('standard_value'),
                            "reason": "value mismatch"
                        })
            else:
                missing_attributes.append({
                    "name": char_name,
                    "tender_value": tender_char.value,
                    "reason": "not found in product"
                })

        # Рассчитываем итоговый score
        required_count = sum(1 for ch in tender_item.characteristics if ch.required)
        if required_count > 0:
            match_score = total_score / required_count
        else:
            match_score = 1.0 if len(matched_attributes) > 0 else 0.0

        return match_score, {
            "matched_attributes": matched_attributes,
            "missing_attributes": missing_attributes,
            "total_required": required_count,
            "total_matched": len(matched_attributes)
        }

    async def find_products_by_okpd2_with_fallback(
            self,
            okpd2_code: str,
            min_results: int = 5,
            max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Интеллектуальный поиск товаров по OKPD2 с расширением области поиска
        """
        # Проверяем кэш
        if self._okpd2_cache is not None and okpd2_code in self._okpd2_cache:
            cached_data, cache_time = self._okpd2_cache[okpd2_code]
            if (datetime.utcnow() - cache_time).total_seconds() < settings.okpd2_cache_ttl:
                logger.debug(f"Using cached results for OKPD2 {okpd2_code}")
                return cached_data

        # Разбираем код OKPD2 на части
        okpd2_parts = okpd2_code.split('.')

        # Убираем КТРУ часть если есть
        okpd2_base = okpd2_code
        if '-' in okpd2_code:
            okpd2_base, ktru_part = okpd2_code.split('-', 1)
            okpd2_parts = okpd2_base.split('.')

        # Строим иерархию поиска от точного к широкому
        search_patterns = []

        # 1. Точное совпадение (с КТРУ если есть)
        search_patterns.append(okpd2_code)

        # 2. Без КТРУ части
        if '-' in okpd2_code:
            search_patterns.append(okpd2_base)

        # 3. По уровням иерархии OKPD2 (если включен fallback)
        if settings.okpd2_fallback_enabled:
            if len(okpd2_parts) >= 3 and settings.okpd2_search_depth >= 3:
                # Уровень подгруппы (9 цифр)
                search_patterns.append('.'.join(okpd2_parts[:3]))

            if len(okpd2_parts) >= 2 and settings.okpd2_search_depth >= 2:
                # Уровень группы (6 цифр)
                search_patterns.append('.'.join(okpd2_parts[:2]))

            if len(okpd2_parts) >= 1 and settings.okpd2_search_depth >= 1:
                # Уровень подкласса (4 цифры)
                search_patterns.append(okpd2_parts[0])

        # Ищем, расширяя область поиска при необходимости
        all_products = []
        used_patterns = []

        for pattern in search_patterns:
            if len(all_products) >= min_results:
                break

            logger.debug(f"Searching with pattern: {pattern}")

            products = await self.unique_products_store.find_products(
                filters={"okpd2_code": {"$regex": f"^{pattern}"}},
                limit=max_results - len(all_products)
            )

            # Добавляем только новые товары (по product_hash)
            existing_hashes = {p['product_hash'] for p in all_products}
            new_products = [p for p in products if p['product_hash'] not in existing_hashes]

            if new_products:
                all_products.extend(new_products)
                used_patterns.append(pattern)
                logger.info(f"Found {len(new_products)} products with pattern '{pattern}'")

        # Логируем результат поиска
        logger.info(
            f"OKPD2 search complete: '{okpd2_code}' -> {len(all_products)} products "
            f"(patterns used: {used_patterns})"
        )

        # Сохраняем в кэш
        if self._okpd2_cache is not None:
            self._okpd2_cache[okpd2_code] = (all_products[:max_results], datetime.utcnow())

        return all_products[:max_results]

    async def match_tender_item(self, tender_item: TenderItem) -> TenderItemMatch:
        """Найти подходящие товары для позиции тендера"""
        try:
            logger.info(f"Matching tender item {tender_item.id}: {tender_item.name}")

            # Используем улучшенный поиск с fallback
            products = await self.find_products_by_okpd2_with_fallback(
                okpd2_code=tender_item.okpd2Code,
                min_results=settings.min_products_for_matching,
                max_results=settings.max_matched_products_per_item * 10
            )

            if not products:
                logger.warning(f"No products found for OKPD2 code {tender_item.okpd2Code}")
                return TenderItemMatch(
                    tender_item_id=tender_item.id,
                    tender_item_name=tender_item.name,
                    okpd2_code=tender_item.okpd2Code,
                    matched_products=[],
                    total_matches=0,
                    best_match_score=0.0,
                    processing_status="no_matches"
                )

            # Оцениваем каждый найденный товар
            matched_products = []

            for product in products:
                match_score, match_details = self.calculate_match_score(tender_item, product)

                # Пропускаем товары с низким score
                if match_score < settings.min_match_score:
                    continue

                # Подготавливаем информацию о поставщиках
                matched_suppliers = []
                for supplier in product.get('unique_suppliers', []):
                    # Проверяем цену
                    tender_price = tender_item.unitPrice.get('amount', 0)
                    supplier_offers = supplier.get('supplier_offers', [])

                    best_price = None
                    for offer in supplier_offers:
                        if isinstance(offer, dict) and 'price' in offer:
                            for price_info in offer['price']:
                                if isinstance(price_info, dict) and 'price' in price_info:
                                    price = price_info['price']
                                    if best_price is None or price < best_price:
                                        best_price = price

                    # Оцениваем поставщика
                    supplier_score = match_score
                    if best_price and tender_price > 0:
                        price_ratio = best_price / tender_price
                        # Применяем допустимое отклонение цены из настроек
                        max_price_ratio = 1 + (settings.price_tolerance_percent / 100)

                        if price_ratio <= max_price_ratio:
                            # Бонус за низкую цену (чем ниже цена, тем выше бонус)
                            supplier_score *= (2.0 - price_ratio / max_price_ratio)

                    matched_suppliers.append(MatchedSupplier(
                        supplier_key=supplier.get('supplier_key', ''),
                        supplier_name=supplier.get('supplier_name', ''),
                        supplier_tel=supplier.get('supplier_tel'),
                        supplier_address=supplier.get('supplier_address'),
                        supplier_offers=supplier.get('supplier_offers', []),
                        purchase_url=supplier.get('purchase_url'),
                        match_score=supplier_score,
                        matched_attributes=[attr['name'] for attr in match_details['matched_attributes']]
                    ))

                # Сортируем поставщиков по score
                matched_suppliers.sort(key=lambda x: x.match_score, reverse=True)

                matched_products.append(MatchedProduct(
                    product_hash=product['product_hash'],
                    okpd2_code=product['okpd2_code'],
                    okpd2_name=product.get('okpd2_name', ''),
                    sample_title=product.get('sample_title'),
                    sample_brand=product.get('sample_brand'),
                    standardized_attributes=product.get('standardized_attributes', []),
                    matched_suppliers=matched_suppliers,
                    total_suppliers=len(matched_suppliers),
                    match_score=match_score,
                    match_details=match_details
                ))

            # Сортируем товары по score
            matched_products.sort(key=lambda x: x.match_score, reverse=True)

            # Берем топ N лучших совпадений из настроек
            matched_products = matched_products[:settings.max_matched_products_per_item]

            return TenderItemMatch(
                tender_item_id=tender_item.id,
                tender_item_name=tender_item.name,
                okpd2_code=tender_item.okpd2Code,
                matched_products=matched_products,
                total_matches=len(matched_products),
                best_match_score=matched_products[0].match_score if matched_products else 0.0,
                processing_status="success"
            )

        except Exception as e:
            logger.error(f"Error matching tender item {tender_item.id}: {e}", exc_info=True)
            return TenderItemMatch(
                tender_item_id=tender_item.id,
                tender_item_name=tender_item.name,
                okpd2_code=tender_item.okpd2Code,
                matched_products=[],
                total_matches=0,
                best_match_score=0.0,
                processing_status="error",
                error_message=str(e)
            )

    async def process_tender_sequential(self, tender_request: TenderRequest) -> TenderMatchingResult:
        """Последовательная обработка тендера (для небольших тендеров)"""
        logger.info(f"Processing tender {tender_request.tenderInfo.tenderNumber} (sequential mode)")
        start_time = datetime.utcnow()

        # Обрабатываем каждый товар
        item_matches = []
        for item in tender_request.items:
            if not item.okpd2Code:  # Пропускаем товары без OKPD2
                logger.debug(f"Skipping item {item.id} without OKPD2 code")
                continue

            match_result = await self.match_tender_item(item)
            item_matches.append(match_result)

        # Подсчитываем статистику
        total_items = len([item for item in tender_request.items if item.okpd2Code])
        matched_items = sum(1 for m in item_matches if m.total_matches > 0)

        # Формируем сводку
        summary = {
            "total_suppliers": sum(
                sum(p.total_suppliers for p in m.matched_products)
                for m in item_matches
            ),
            "average_match_score": sum(m.best_match_score for m in item_matches) / len(
                item_matches) if item_matches else 0,
            "items_with_perfect_match": sum(1 for m in item_matches if m.best_match_score >= 0.9),
            "items_with_good_match": sum(1 for m in item_matches if 0.7 <= m.best_match_score < 0.9),
            "items_with_partial_match": sum(1 for m in item_matches if 0.5 <= m.best_match_score < 0.7),
            "items_without_match": sum(1 for m in item_matches if m.best_match_score == 0),
            "processing_duration_seconds": (datetime.utcnow() - start_time).total_seconds()
        }

        return TenderMatchingResult(
            tender_number=tender_request.tenderInfo.tenderNumber,
            tender_name=tender_request.tenderInfo.tenderName,
            processing_time=datetime.utcnow(),
            total_items=total_items,
            matched_items=matched_items,
            item_matches=item_matches,
            summary=summary
        )

    async def process_tender_parallel(self, tender_request: TenderRequest) -> TenderMatchingResult:
        """Параллельная обработка тендера (для больших тендеров)"""
        logger.info(f"Processing tender {tender_request.tenderInfo.tenderNumber} (parallel mode)")
        start_time = datetime.utcnow()

        # Фильтруем товары с OKPD2
        valid_items = [item for item in tender_request.items if item.okpd2Code]

        if not valid_items:
            return TenderMatchingResult(
                tender_number=tender_request.tenderInfo.tenderNumber,
                tender_name=tender_request.tenderInfo.tenderName,
                processing_time=datetime.utcnow(),
                total_items=0,
                matched_items=0,
                item_matches=[],
                summary={"error": "No valid items to process"}
            )

        # Определяем размер батчей для параллельной обработки
        batch_size = min(settings.max_parallel_items, len(valid_items))

        # Обрабатываем батчами
        all_matches = []

        for i in range(0, len(valid_items), batch_size):
            batch = valid_items[i:i + batch_size]
            batch_start = time.time()

            # Создаем задачи для параллельного выполнения
            tasks = [self.match_tender_item(item) for item in batch]

            # Выполняем параллельно
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Обрабатываем результаты
            for idx, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    logger.error(f"Error processing item {batch[idx].id}: {result}")
                    # Создаем результат с ошибкой
                    all_matches.append(TenderItemMatch(
                        tender_item_id=batch[idx].id,
                        tender_item_name=batch[idx].name,
                        okpd2_code=batch[idx].okpd2Code,
                        matched_products=[],
                        total_matches=0,
                        best_match_score=0.0,
                        processing_status="error",
                        error_message=str(result)
                    ))
                else:
                    all_matches.append(result)

            batch_time = time.time() - batch_start
            logger.info(f"Processed batch {i // batch_size + 1}: {len(batch)} items in {batch_time:.2f}s")

            # Небольшая задержка между батчами для снижения нагрузки
            if i + batch_size < len(valid_items):
                await asyncio.sleep(0.1)

        # Подсчитываем статистику
        total_items = len(valid_items)
        matched_items = sum(1 for m in all_matches if m.total_matches > 0)

        # Формируем сводку
        processing_time = (datetime.utcnow() - start_time).total_seconds()

        summary = {
            "total_suppliers": sum(
                sum(p.total_suppliers for p in m.matched_products)
                for m in all_matches
            ),
            "average_match_score": sum(m.best_match_score for m in all_matches) / len(
                all_matches) if all_matches else 0,
            "items_with_perfect_match": sum(1 for m in all_matches if m.best_match_score >= 0.9),
            "items_with_good_match": sum(1 for m in all_matches if 0.7 <= m.best_match_score < 0.9),
            "items_with_partial_match": sum(1 for m in all_matches if 0.5 <= m.best_match_score < 0.7),
            "items_without_match": sum(1 for m in all_matches if m.best_match_score == 0),
            "items_with_errors": sum(1 for m in all_matches if m.processing_status == "error"),
            "processing_duration_seconds": processing_time,
            "items_per_second": total_items / processing_time if processing_time > 0 else 0,
            "parallel_batch_size": batch_size
        }

        logger.info(
            f"Tender processed in {processing_time:.2f}s "
            f"({summary['items_per_second']:.1f} items/sec)"
        )

        return TenderMatchingResult(
            tender_number=tender_request.tenderInfo.tenderNumber,
            tender_name=tender_request.tenderInfo.tenderName,
            processing_time=datetime.utcnow(),
            total_items=total_items,
            matched_items=matched_items,
            item_matches=all_matches,
            summary=summary
        )

    async def process_tender(self, tender_request: TenderRequest) -> TenderMatchingResult:
        """Обработать весь тендер"""
        # Определяем режим обработки
        items_count = len([item for item in tender_request.items if item.okpd2Code])

        # Используем параллельную обработку для больших тендеров
        if settings.enable_parallel_processing and items_count > settings.max_parallel_items:
            return await self.process_tender_parallel(tender_request)
        else:
            # Обычная последовательная обработка для небольших тендеров
            return await self.process_tender_sequential(tender_request)