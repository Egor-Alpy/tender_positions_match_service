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
from src.services.term_extractor import TenderTermExtractor
from src.services.semantic_search import SemanticSearchService
from src.services.attribute_matcher import EnhancedAttributeMatcher
from src.core.config import settings

logger = logging.getLogger(__name__)


class TenderMatchingService:
    """Сервис для сопоставления товаров из тендера с товарами из БД"""

    def __init__(self, unique_products_store: UniqueProductsMongoStore):
        self.unique_products_store = unique_products_store
        self._okpd2_cache = {} if settings.enable_okpd2_cache else None

        # Инициализируем новые компоненты если включены
        self.enable_semantic_search = getattr(settings, 'enable_semantic_search', False)
        self.semantic_threshold = getattr(settings, 'semantic_threshold', 0.35)
        self.max_semantic_candidates = getattr(settings, 'max_semantic_candidates', 200)

        if self.enable_semantic_search:
            try:
                self.term_extractor = TenderTermExtractor()
                self.semantic_service = SemanticSearchService()
                # ОТКЛЮЧЕНО: self.attribute_matcher = EnhancedAttributeMatcher()
                logger.info("Семантический поиск включен (без проверки характеристик)")
            except Exception as e:
                logger.warning(f"Не удалось инициализировать семантический поиск: {e}")
                self.enable_semantic_search = False
        else:
            logger.info("Семантический поиск отключен")

    def parse_numeric_condition(self, value: str) -> Tuple[str, float, Optional[float]]:
        """
        Парсить числовое условие из строки
        Возвращает: (оператор, значение1, значение2_опционально)
        """
        if not value:
            return 'string', '', None

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
        if not tender_value or not product_value:
            return False

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

        # ОТКЛЮЧЕНА ПРОВЕРКА ХАРАКТЕРИСТИК - все товары получают базовый score
        # Возвращаем фиксированный score 0.8 для всех товаров
        return 0.8, {
            "matched_attributes": [],
            "missing_attributes": [],
            "total_required": 0,
            "total_matched": 0,
            "note": "Characteristics matching disabled - all products get base score"
        }

    async def find_products_by_okpd2_with_fallback(
            self,
            okpd2_code: str,
            min_results: int = 5,
            max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Временно отключена фильтрация по OKPD2 - возвращаем топ 10 товаров
        """
        logger.info(f"ВРЕМЕННО: поиск без фильтрации OKPD2, возвращаем топ 10 товаров")
        
        # Получаем первые 10 товаров из базы без фильтрации по OKPD2
        products = await self.unique_products_store.find_products(
            filters={},  # Пустые фильтры - без ограничений по OKPD2
            limit=10
        )
        
        logger.info(f"Найдено {len(products)} товаров без фильтрации OKPD2")
        return products

    async def match_tender_item(self, tender_item: TenderItem) -> TenderItemMatch:
        """Найти подходящие товары для позиции тендера"""

        # Проверяем обязательные поля (временно отключено)
        # if not tender_item.okpd2Code:
        #     return TenderItemMatch(
        #         tender_item_id=tender_item.id,
        #         tender_item_name=tender_item.name,
        #         okpd2_code="",
        #         matched_products=[],
        #         total_matches=0,
        #         best_match_score=0.0,
        #         processing_status="no_matches",
        #         error_message="No OKPD2 code provided"
        #     )

        # Если семантический поиск включен - используем улучшенный алгоритм
        if self.enable_semantic_search and hasattr(self, 'term_extractor'):
            return await self._match_tender_item_enhanced(tender_item)

        # Иначе используем стандартный алгоритм
        return await self._match_tender_item_standard(tender_item)

    async def _match_tender_item_standard(self, tender_item: TenderItem) -> TenderItemMatch:
        """Стандартный алгоритм поиска (оригинальный)"""
        try:
            # Обрабатываем None значения
            item_id = tender_item.id if tender_item.id is not None else 0
            item_name = tender_item.name if tender_item.name else "Без названия"
            okpd2_code = tender_item.okpd2Code if tender_item.okpd2Code else ""

            logger.info(f"Matching tender item {item_id}: {item_name}")

            # Используем улучшенный поиск с fallback
            products = await self.find_products_by_okpd2_with_fallback(
                okpd2_code=okpd2_code,
                min_results=settings.min_products_for_matching,
                max_results=settings.max_matched_products_per_item * 10
            )

            if not products:
                logger.warning(f"No products found for OKPD2 code {okpd2_code}")
                return TenderItemMatch(
                    tender_item_id=item_id,
                    tender_item_name=item_name,
                    okpd2_code=okpd2_code,
                    matched_products=[],
                    total_matches=0,
                    best_match_score=0.0,
                    processing_status="no_matches"
                )

            # Оцениваем каждый найденный товар
            matched_products = []

            for product in products:
                match_score, match_details = self.calculate_match_score(tender_item, product)

                # Временно отключаем фильтрацию по минимальному score - принимаем ВСЕ товары
                # if match_score < settings.min_match_score:
                #     continue

                # Подготавливаем информацию о поставщиках
                matched_suppliers = []
                for supplier in product.get('unique_suppliers', []):
                    # Проверяем цену
                    tender_price = tender_item.unitPrice.get('amount') if tender_item.unitPrice else None
                    tender_price = tender_price if tender_price is not None else 0
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
                        matched_attributes=[attr['name'] for attr in match_details.get('matched_attributes', [])]
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
                tender_item_id=item_id,
                tender_item_name=item_name,
                okpd2_code=okpd2_code,
                matched_products=matched_products,
                total_matches=len(matched_products),
                best_match_score=matched_products[0].match_score if matched_products else 0.0,
                processing_status="success"
            )

        except Exception as e:
            logger.error(f"Error matching tender item {tender_item.id}: {e}", exc_info=True)
            return TenderItemMatch(
                tender_item_id=tender_item.id,
                tender_item_name=tender_item.name if tender_item.name else "Без названия",
                okpd2_code=tender_item.okpd2Code if tender_item.okpd2Code else "",
                matched_products=[],
                total_matches=0,
                best_match_score=0.0,
                processing_status="error",
                error_message=str(e)
            )

    async def _match_tender_item_enhanced(self, tender_item: TenderItem) -> TenderItemMatch:
        """Улучшенный алгоритм с семантическим поиском"""

        start_time = time.time()
        processing_stats = {}

        try:
            # Обрабатываем None значения
            item_id = tender_item.id if tender_item.id is not None else 0
            item_name = tender_item.name if tender_item.name else "Без названия"
            okpd2_code = tender_item.okpd2Code if tender_item.okpd2Code else ""

            logger.info(f"Enhanced matching for item {item_id}: {item_name}")

            # ЭТАП 1: Извлечение терминов
            search_terms = self.term_extractor.extract_from_tender_item(tender_item.dict())
            processing_stats['search_query'] = search_terms['search_query']
            processing_stats['weighted_terms_count'] = len(search_terms['weighted_terms'])

            # ЭТАП 2: Расширенный поиск в БД
            # Проверяем поддержку расширенного поиска
            if hasattr(self.unique_products_store, 'find_products_enhanced'):
                products = await self.unique_products_store.find_products_enhanced(
                    okpd2_code=okpd2_code,
                    search_terms=search_terms.get('all_terms', []),
                    weighted_terms=search_terms.get('weighted_terms', {}),
                    limit=1000
                )
            else:
                # Fallback на стандартный поиск
                products = await self.find_products_by_okpd2_with_fallback(
                    okpd2_code=okpd2_code,
                    min_results=100,
                    max_results=1000
                )

            processing_stats['candidates_found'] = len(products)

            if not products:
                logger.warning(f"No products found for enhanced search")
                return TenderItemMatch(
                    tender_item_id=item_id,
                    tender_item_name=item_name,
                    okpd2_code=okpd2_code,
                    matched_products=[],
                    total_matches=0,
                    best_match_score=0.0,
                    processing_status="no_matches",
                    processing_stats=processing_stats
                )

            # ЭТАП 3: Семантическая фильтрация
            if len(products) > 50:
                products = await self.semantic_service.compute_similarities(
                    tender_item.dict(),
                    products
                )

                products = self.semantic_service.filter_by_similarity(
                    products,
                    threshold=self.semantic_threshold,
                    top_k=self.max_semantic_candidates
                )

                products = self.semantic_service.combine_scores(products)
                processing_stats['after_semantic_filter'] = len(products)

            # ЭТАП 4: Точное сопоставление
            matched_products = []

            for product in products[:100]:
                match_score, match_details = self.calculate_match_score(tender_item, product)

                # Временно отключаем фильтрацию по минимальному score - принимаем ВСЕ товары
                # if match_score >= settings.min_match_score:
                if True:  # Всегда обрабатываем товар
                    # Комбинированный скор
                    semantic_score = product.get('semantic_score', 0.5)
                    text_score = product.get('weighted_score', 0.5)

                    final_score = (
                            0.4 * match_score +  # Характеристики
                            0.3 * text_score +  # Текстовое совпадение
                            0.3 * semantic_score  # Семантическая близость
                    )

                    # Обрабатываем поставщиков
                    matched_suppliers = []
                    for supplier in product.get('unique_suppliers', []):
                        tender_price = tender_item.unitPrice.get('amount') if tender_item.unitPrice else None
                        tender_price = tender_price if tender_price is not None else 0
                        best_price = self._get_best_supplier_price(supplier)

                        supplier_score = final_score
                        if best_price and tender_price > 0:
                            price_ratio = best_price / tender_price
                            max_ratio = 1 + (settings.price_tolerance_percent / 100)

                            if price_ratio <= max_ratio:
                                supplier_score *= (2.0 - price_ratio / max_ratio)

                        matched_suppliers.append(MatchedSupplier(
                            supplier_key=supplier.get('supplier_key', ''),
                            supplier_name=supplier.get('supplier_name', ''),
                            supplier_tel=supplier.get('supplier_tel'),
                            supplier_address=supplier.get('supplier_address'),
                            supplier_offers=supplier.get('supplier_offers', []),
                            purchase_url=supplier.get('purchase_url'),
                            match_score=supplier_score,
                            matched_attributes=[
                                attr['name'] for attr in match_details.get('matched_attributes', [])
                            ] if isinstance(match_details, dict) else []
                        ))

                    matched_suppliers.sort(key=lambda x: x.match_score, reverse=True)

                    # Добавляем детали в match_details
                    if isinstance(match_details, dict):
                        match_details['text_score'] = text_score
                        match_details['semantic_score'] = semantic_score
                        match_details['final_score'] = final_score

                    matched_products.append(MatchedProduct(
                        product_hash=product['product_hash'],
                        okpd2_code=product['okpd2_code'],
                        okpd2_name=product.get('okpd2_name', ''),
                        sample_title=product.get('sample_title'),
                        sample_brand=product.get('sample_brand'),
                        standardized_attributes=product.get('standardized_attributes', []),
                        matched_suppliers=matched_suppliers,
                        total_suppliers=len(matched_suppliers),
                        match_score=final_score,
                        match_details=match_details
                    ))

            # Сортируем и ограничиваем
            matched_products.sort(key=lambda x: x.match_score, reverse=True)
            matched_products = matched_products[:settings.max_matched_products_per_item]

            processing_stats['matched_products'] = len(matched_products)
            processing_stats['processing_time'] = time.time() - start_time

            return TenderItemMatch(
                tender_item_id=item_id,
                tender_item_name=item_name,
                okpd2_code=okpd2_code,
                matched_products=matched_products,
                total_matches=len(matched_products),
                best_match_score=matched_products[0].match_score if matched_products else 0.0,
                processing_status="success",
                processing_stats=processing_stats
            )

        except Exception as e:
            logger.error(f"Error in enhanced matching: {e}", exc_info=True)
            # Fallback на стандартный алгоритм
            return await self._match_tender_item_standard(tender_item)

    def _get_best_supplier_price(self, supplier: Dict[str, Any]) -> Optional[float]:
        """Получить лучшую цену поставщика"""

        best_price = None

        for offer in supplier.get('supplier_offers', []):
            if isinstance(offer, dict) and 'price' in offer:
                for price_info in offer['price']:
                    if isinstance(price_info, dict) and 'price' in price_info:
                        price = price_info['price']
                        if best_price is None or price < best_price:
                            best_price = price

        return best_price

    async def process_tender_sequential(self, tender_request: TenderRequest) -> TenderMatchingResult:
        """Последовательная обработка тендера (для небольших тендеров)"""
        # Получаем значения с обработкой None
        tender_number = None
        tender_name = None

        if tender_request.tenderInfo:
            tender_number = tender_request.tenderInfo.tenderNumber
            tender_name = tender_request.tenderInfo.tenderName

        logger.info(f"Processing tender {tender_number or 'No number'} (sequential mode)")
        start_time = datetime.utcnow()

        # Обрабатываем каждый товар (временно без проверки ОКПД2)
        item_matches = []
        for item in tender_request.items:
            # if not item.okpd2Code:  # Пропускаем товары без OKPD2
            #     logger.debug(f"Skipping item {item.id} without OKPD2 code")
            #     continue

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

        # Добавляем информацию о семантическом поиске если включен
        if self.enable_semantic_search:
            summary["semantic_search_enabled"] = True
            summary["algorithm_version"] = "enhanced"

            # Собираем статистику по семантике
            total_analyzed = sum(
                m.processing_stats.get('candidates_found', 0)
                for m in item_matches
                if hasattr(m, 'processing_stats') and m.processing_stats
            )

            total_filtered = sum(
                m.processing_stats.get('after_semantic_filter', 0)
                for m in item_matches
                if hasattr(m, 'processing_stats') and m.processing_stats
            )

            if total_analyzed > 0:
                summary["total_products_analyzed"] = total_analyzed
                summary["total_semantic_filtered"] = total_filtered

        return TenderMatchingResult(
            tender_number=tender_number,
            tender_name=tender_name,
            processing_time=datetime.utcnow(),
            total_items=total_items,
            matched_items=matched_items,
            item_matches=item_matches,
            summary=summary
        )

    async def process_tender_parallel(self, tender_request: TenderRequest) -> TenderMatchingResult:
        """Параллельная обработка тендера (для больших тендеров)"""
        # Получаем значения с обработкой None
        tender_number = None
        tender_name = None

        if tender_request.tenderInfo:
            tender_number = tender_request.tenderInfo.tenderNumber
            tender_name = tender_request.tenderInfo.tenderName

        logger.info(f"Processing tender {tender_number or 'No number'} (parallel mode)")
        start_time = datetime.utcnow()

        # Фильтруем товары с OKPD2 (временно отключено)
        valid_items = [item for item in tender_request.items]  # Берем все товары

        if not valid_items:
            return TenderMatchingResult(
                tender_number=tender_number,
                tender_name=tender_name,
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
                        tender_item_name=batch[idx].name if batch[idx].name else "Без названия",
                        okpd2_code=batch[idx].okpd2Code if batch[idx].okpd2Code else "",
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
            tender_number=tender_number,
            tender_name=tender_name,
            processing_time=datetime.utcnow(),
            total_items=total_items,
            matched_items=matched_items,
            item_matches=all_matches,
            summary=summary
        )

    async def process_tender(self, tender_request: TenderRequest) -> TenderMatchingResult:
        """Обработать весь тендер"""
        # Определяем режим обработки (временно без фильтрации по ОКПД2)
        items_count = len([item for item in tender_request.items])  # Берем все товары

        # Используем параллельную обработку для больших тендеров
        if settings.enable_parallel_processing and items_count > settings.max_parallel_items:
            return await self.process_tender_parallel(tender_request)
        else:
            # Обычная последовательная обработка для небольших тендеров
            return await self.process_tender_sequential(tender_request)