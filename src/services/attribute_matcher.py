import re
import logging
from typing import Dict, List, Any, Optional, Tuple
from difflib import SequenceMatcher
from dataclasses import dataclass

from src.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AttributeMatchResult:
    """Результат сопоставления атрибута"""
    matched: bool
    score: float
    confidence: float
    reason: str
    matched_attribute: Optional[Dict[str, Any]] = None
    details: Optional[Dict[str, Any]] = None


class EnhancedAttributeMatcher:
    """Улучшенный матчер для стандартизированных атрибутов"""

    def __init__(self):
        self.logger = logger

        # Настройки весов
        self.weights = {
            'exact_match': 1.0,
            'synonym_match': 0.9,
            'partial_match': 0.7,
            'fuzzy_match': 0.6,
            'no_match': 0.0
        }

        # Пороги
        self.thresholds = {
            'fuzzy_name': 0.8,
            'fuzzy_value': 0.85,
            'numeric_tolerance': 0.1
        }

        # Синонимы значений
        self.value_synonyms = {
            'черный': ['черная', 'черное', 'black', 'темный'],
            'белый': ['белая', 'белое', 'white', 'светлый'],
            'красный': ['красная', 'красное', 'red', 'алый'],
            'синий': ['синяя', 'синее', 'blue', 'голубой'],
            'зеленый': ['зеленая', 'зеленое', 'green'],
            'да': ['yes', 'есть', 'присутствует', 'имеется', '+'],
            'нет': ['no', 'отсутствует', 'без', '-'],
            'а4': ['a4', 'а-4', 'a-4', '210x297'],
            'а3': ['a3', 'а-3', 'a-3', '297x420']
        }

        # Конверсии единиц измерения
        self.unit_conversions = {
            'мм': {'см': 0.1, 'м': 0.001},
            'см': {'мм': 10, 'м': 0.01},
            'м': {'мм': 1000, 'см': 100},
            'г': {'кг': 0.001},
            'кг': {'г': 1000}
        }

        self.logger.info("EnhancedAttributeMatcher инициализирован")

    def match_characteristics(self, tender_item: Dict[str, Any],
                              product: Dict[str, Any]) -> Dict[str, Any]:
        """Сопоставить характеристики тендера и товара"""

        tender_chars = tender_item.get('characteristics', [])
        product_attrs = product.get('standardized_attributes', [])

        self.logger.debug(f"Сопоставление: {len(tender_chars)} характеристик тендера "
                          f"с {len(product_attrs)} атрибутами товара")

        if not tender_chars:
            # Нет требований - товар подходит
            return {
                'is_suitable': True,
                'match_score': 1.0,
                'confidence': 1.0,
                'matched_required': 0,
                'total_required': 0,
                'details': []
            }

        # Разделяем на обязательные и опциональные
        required_chars = [c for c in tender_chars if c.get('required', False)]
        optional_chars = [c for c in tender_chars if not c.get('required', False)]

        # Результаты
        results = {
            'is_suitable': False,
            'match_score': 0.0,
            'confidence': 0.0,
            'matched_required': 0,
            'total_required': len(required_chars),
            'matched_optional': 0,
            'total_optional': len(optional_chars),
            'details': []
        }

        total_score = 0.0
        total_confidence = 0.0

        # Проверяем обязательные характеристики
        for char in required_chars:
            match_result = self._match_single_characteristic(char, product_attrs)

            if match_result.matched:
                results['matched_required'] += 1
                total_score += match_result.score

            total_confidence += match_result.confidence

            results['details'].append({
                'characteristic': char,
                'matched': match_result.matched,
                'score': match_result.score,
                'confidence': match_result.confidence,
                'reason': match_result.reason,
                'matched_with': match_result.matched_attribute,
                'required': True
            })

        # Проверяем опциональные характеристики
        for char in optional_chars:
            match_result = self._match_single_characteristic(char, product_attrs)

            if match_result.matched:
                results['matched_optional'] += 1
                total_score += match_result.score * 0.5  # Меньший вес для опциональных

            total_confidence += match_result.confidence * 0.5

            results['details'].append({
                'characteristic': char,
                'matched': match_result.matched,
                'score': match_result.score,
                'confidence': match_result.confidence,
                'reason': match_result.reason,
                'matched_with': match_result.matched_attribute,
                'required': False
            })

        # Товар подходит если все обязательные характеристики совпали
        results['is_suitable'] = (results['matched_required'] == results['total_required'])

        # Рассчитываем итоговые скоры
        total_chars = len(tender_chars)
        if total_chars > 0:
            results['match_score'] = total_score / total_chars
            results['confidence'] = total_confidence / total_chars

        # Процент совпадения
        total_matched = results['matched_required'] + results['matched_optional']
        results['match_percentage'] = (total_matched / total_chars * 100) if total_chars > 0 else 0

        self.logger.debug(f"Результат: подходит={results['is_suitable']}, "
                          f"обязательных={results['matched_required']}/{results['total_required']}, "
                          f"скор={results['match_score']:.2f}")

        return results

    def _match_single_characteristic(self, tender_char: Dict[str, Any],
                                     product_attrs: List[Dict[str, Any]]) -> AttributeMatchResult:
        """Сопоставить одну характеристику"""

        char_name = tender_char.get('name', '').strip().lower()
        char_value = str(tender_char.get('value', '')).strip()
        char_type = tender_char.get('type', 'Качественная')

        if not char_name:
            return AttributeMatchResult(
                matched=False,
                score=0.0,
                confidence=0.0,
                reason="Пустое название характеристики"
            )

        # Ищем соответствующий атрибут в товаре
        best_match = None
        best_score = 0.0

        for attr in product_attrs:
            attr_name = attr.get('standard_name', '').strip().lower()
            attr_value = str(attr.get('standard_value', '')).strip()

            # Сравниваем названия
            name_similarity = self._compare_names(char_name, attr_name)

            if name_similarity >= self.thresholds['fuzzy_name']:
                # Сравниваем значения
                if char_type == 'Количественная':
                    value_match = self._match_numeric_value(
                        char_value, attr_value,
                        tender_char.get('unit', ''), attr.get('unit', '')
                    )
                else:
                    value_match = self._match_categorical_value(char_value, attr_value)

                # Комбинированный скор
                combined_score = name_similarity * value_match['score']

                if combined_score > best_score:
                    best_score = combined_score
                    best_match = {
                        'attribute': attr,
                        'name_similarity': name_similarity,
                        'value_match': value_match,
                        'combined_score': combined_score
                    }

        # Формируем результат
        if best_match and best_match['value_match']['matched']:
            return AttributeMatchResult(
                matched=True,
                score=best_match['combined_score'],
                confidence=best_match['value_match']['confidence'],
                reason=best_match['value_match']['reason'],
                matched_attribute=best_match['attribute'],
                details={
                    'name_similarity': best_match['name_similarity'],
                    'value_score': best_match['value_match']['score']
                }
            )
        else:
            return AttributeMatchResult(
                matched=False,
                score=0.0,
                confidence=0.8,
                reason=f"Не найдено соответствие для '{char_name}' = '{char_value}'"
            )

    def _compare_names(self, name1: str, name2: str) -> float:
        """Сравнить названия характеристик"""

        # Точное совпадение
        if name1 == name2:
            return 1.0

        # Проверяем вхождение
        if name1 in name2 or name2 in name1:
            return 0.9

        # Fuzzy matching
        similarity = SequenceMatcher(None, name1, name2).ratio()

        return similarity

    def _match_numeric_value(self, tender_value: str, product_value: str,
                             tender_unit: str, product_unit: str) -> Dict[str, Any]:
        """Сопоставить числовые значения"""

        # Парсим значения
        tender_parsed = self._parse_numeric_condition(tender_value)
        product_parsed = self._parse_numeric_condition(product_value)

        if not tender_parsed['numbers'] or not product_parsed['numbers']:
            return {
                'matched': False,
                'score': 0.0,
                'confidence': 0.0,
                'reason': f"Не удалось извлечь числа"
            }

        # Конвертируем единицы измерения если нужно
        if tender_unit and product_unit and tender_unit != product_unit:
            product_parsed = self._convert_units(
                product_parsed, product_unit, tender_unit
            )

        tender_num = tender_parsed['numbers'][0]
        product_num = product_parsed['numbers'][0]

        # Проверяем условия
        matched = False
        reason = ""

        if tender_parsed['operator'] == 'gte':  # >=
            matched = product_num >= tender_num
            reason = f"{product_num} {'>=' if matched else '<'} {tender_num}"

        elif tender_parsed['operator'] == 'lte':  # <=
            matched = product_num <= tender_num
            reason = f"{product_num} {'<=' if matched else '>'} {tender_num}"

        elif tender_parsed['operator'] == 'gt':  # >
            matched = product_num > tender_num
            reason = f"{product_num} {'>' if matched else '<='} {tender_num}"

        elif tender_parsed['operator'] == 'lt':  # <
            matched = product_num < tender_num
            reason = f"{product_num} {'<' if matched else '>='} {tender_num}"

        elif tender_parsed['operator'] == 'range':  # Диапазон
            if len(tender_parsed['numbers']) >= 2:
                min_val, max_val = tender_parsed['numbers'][:2]
                matched = min_val <= product_num <= max_val
                reason = f"{product_num} {'в' if matched else 'вне'} [{min_val}, {max_val}]"

        else:  # Точное значение
            tolerance = self.thresholds['numeric_tolerance']
            diff = abs(product_num - tender_num) / tender_num if tender_num != 0 else 0
            matched = diff <= tolerance
            reason = f"{product_num} {'≈' if matched else '≠'} {tender_num}"

        confidence = 0.9 if matched else 0.8
        score = self.weights['exact_match'] if matched else self.weights['no_match']

        return {
            'matched': matched,
            'score': score,
            'confidence': confidence,
            'reason': reason
        }

    def _match_categorical_value(self, tender_value: str, product_value: str) -> Dict[str, Any]:
        """Сопоставить категориальные значения"""

        tender_lower = tender_value.lower().strip()
        product_lower = product_value.lower().strip()

        # Точное совпадение
        if tender_lower == product_lower:
            return {
                'matched': True,
                'score': self.weights['exact_match'],
                'confidence': 1.0,
                'reason': "Точное совпадение"
            }

        # Проверяем синонимы
        for base_value, synonyms in self.value_synonyms.items():
            all_values = [base_value] + synonyms
            if tender_lower in all_values and product_lower in all_values:
                return {
                    'matched': True,
                    'score': self.weights['synonym_match'],
                    'confidence': 0.95,
                    'reason': f"Синонимы: '{tender_value}' = '{product_value}'"
                }

        # Проверяем вхождение
        if len(tender_lower) > 3:
            if tender_lower in product_lower or product_lower in tender_lower:
                return {
                    'matched': True,
                    'score': self.weights['partial_match'],
                    'confidence': 0.85,
                    'reason': f"Частичное совпадение"
                }

        # Fuzzy matching
        similarity = SequenceMatcher(None, tender_lower, product_lower).ratio()
        if similarity >= self.thresholds['fuzzy_value']:
            return {
                'matched': True,
                'score': self.weights['fuzzy_match'],
                'confidence': similarity,
                'reason': f"Похожие значения ({similarity:.0%})"
            }

        return {
            'matched': False,
            'score': self.weights['no_match'],
            'confidence': 0.9,
            'reason': f"Не совпадает: '{tender_value}' ≠ '{product_value}'"
        }

    def _parse_numeric_condition(self, value: str) -> Dict[str, Any]:
        """Парсить числовое условие"""

        value_clean = value.lower().strip()
        result = {
            'numbers': [],
            'operator': 'eq'
        }

        # Проверяем операторы
        patterns = [
            (r'>\s*(\d+(?:\.\d+)?)\s*и\s*≤\s*(\d+(?:\.\d+)?)', 'range'),
            (r'≥\s*(\d+(?:\.\d+)?)', 'gte'),
            (r'≤\s*(\d+(?:\.\d+)?)', 'lte'),
            (r'>\s*(\d+(?:\.\d+)?)', 'gt'),
            (r'<\s*(\d+(?:\.\d+)?)', 'lt'),
            (r'^(\d+(?:\.\d+)?)$', 'eq')
        ]

        for pattern, operator in patterns:
            match = re.search(pattern, value_clean)
            if match:
                result['operator'] = operator
                result['numbers'] = [float(g) for g in match.groups()]
                break

        # Если не нашли паттерн, пытаемся извлечь любое число
        if not result['numbers']:
            numbers = re.findall(r'\d+(?:\.\d+)?', value_clean)
            if numbers:
                result['numbers'] = [float(numbers[0])]

        return result

    def _convert_units(self, value_dict: Dict[str, Any],
                       from_unit: str, to_unit: str) -> Dict[str, Any]:
        """Конвертировать единицы измерения"""

        from_unit = from_unit.lower()
        to_unit = to_unit.lower()

        if from_unit == to_unit:
            return value_dict

        # Проверяем прямую конверсию
        if from_unit in self.unit_conversions:
            if to_unit in self.unit_conversions[from_unit]:
                factor = self.unit_conversions[from_unit][to_unit]
                value_dict['numbers'] = [n * factor for n in value_dict['numbers']]

        return value_dict
