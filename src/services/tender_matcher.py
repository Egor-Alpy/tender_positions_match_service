import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import re

from src.models.tender import (
    TenderRequest, TenderItem, TenderItemMatch,
    MatchedProduct, MatchedSupplier, TenderMatchingResult
)
from src.storage.unique_products_mongo import UniqueProductsMongoStore

logger = logging.getLogger(__name__)


class TenderMatchingService:
    """Сервис для сопоставления товаров из тендера с товарами из БД"""

    def __init__(self, unique_products_store: UniqueProductsMongoStore):
        self.unique_products_store = unique_products_store

        # Маппинг типов характеристик для нормализации
        self.characteristic_mappings = {
            # Размеры
            "Ширина": ["ширина", "width"],
            "Длина": ["длина", "length", "длина намотки"],
            "Высота": ["высота", "height"],
            "Толщина": ["толщина", "thickness"],
            "Диаметр": ["диаметр", "diameter"],

            # Вес и объем
            "Масса": ["масса", "вес", "weight", "mass"],
            "Объем": ["объем", "объём", "volume"],
            "Плотность": ["плотность", "density", "плотность картона"],

            # Цвет и материал
            "Цвет": ["цвет", "color", "colour"],
            "Материал": ["материал", "material"],
            "Тип": ["тип", "вид", "type"],

            # Прочие
            "Количество": ["количество", "кол-во", "count", "quantity"],
            "Прозрачность": ["прозрачность", "transparency"],
            "Формат": ["формат", "format"],
            "Механизм": ["механизм", "mechanism"]
        }

    def normalize_characteristic_name(self, name: str) -> str:
        """Нормализовать название характеристики"""
        name_lower = name.lower().strip()

        # Ищем соответствие в маппинге
        for standard_name, variations in self.characteristic_mappings.items():
            if any(var in name_lower for var in variations):
                return standard_name

        # Если не нашли - возвращаем как есть, но нормализованное
        return name_lower.replace("_", " ").replace("-", " ")

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
        if not product['okpd2_code'].startswith(tender_item.okpd2Code):
            return 0.0, {
                "matched": False,
                "reason": "OKPD2 code mismatch",
                "tender_okpd2": tender_item.okpd2Code,
                "product_okpd2": product['okpd2_code']
            }

        # 2. Сопоставляем характеристики
        tender_chars = {
            self.normalize_characteristic_name(ch.name): ch
            for ch in tender_item.characteristics
        }

        product_attrs = {
            self.normalize_characteristic_name(attr.get('standard_name', '')): attr
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
                    if self.check_numeric_match(tender_char.value, product_attr.get('standard_value', '')):
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
                    tender_val = tender_char.value.lower().strip()
                    product_val = str(product_attr.get('standard_value', '')).lower().strip()

                    if tender_val == product_val or "эквивалент" in tender_val:
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

    async def match_tender_item(self, tender_item: TenderItem) -> TenderItemMatch:
        """Найти подходящие товары для позиции тендера"""
        try:
            logger.info(f"Matching tender item {tender_item.id}: {tender_item.name}")

            # 1. Ищем товары с таким же OKPD2 кодом
            products = await self.unique_products_store.find_products(
                filters={"okpd2_code": {"$regex": f"^{tender_item.okpd2Code}"}},
                limit=100
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

            # 2. Оцениваем каждый найденный товар
            matched_products = []

            for product in products:
                match_score, match_details = self.calculate_match_score(tender_item, product)

                # Пропускаем товары с низким score
                if match_score < 0.5:  # Минимум 50% совпадений
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
                        if price_ratio <= 1.0:  # Цена не выше тендерной
                            supplier_score *= (2.0 - price_ratio)  # Бонус за низкую цену

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

            # Берем топ-10 лучших совпадений
            matched_products = matched_products[:10]

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
            logger.error(f"Error matching tender item {tender_item.id}: {e}")
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

    async def process_tender(self, tender_request: TenderRequest) -> TenderMatchingResult:
        """Обработать весь тендер"""
        logger.info(f"Processing tender {tender_request.tenderInfo.tenderNumber}")
        start_time = datetime.utcnow()

        # Обрабатываем каждый товар
        item_matches = []
        for item in tender_request.items:
            if item.quantity == 0:  # Пропускаем товары с нулевым количеством
                logger.debug(f"Skipping item {item.id} with zero quantity")
                continue

            match_result = await self.match_tender_item(item)
            item_matches.append(match_result)

        # Подсчитываем статистику
        total_items = len([item for item in tender_request.items if item.quantity > 0])
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