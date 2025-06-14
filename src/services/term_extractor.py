import re
import logging
from typing import Dict, List, Any, Set
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)


class TenderTermExtractor:
    """Экстрактор терминов адаптированный для тендеров"""

    def __init__(self):
        self.logger = logger

        # Базовые стоп-слова
        self.stop_words = {
            'и', 'в', 'во', 'не', 'что', 'он', 'на', 'я', 'с', 'со', 'как', 'а', 'то', 'все',
            'она', 'так', 'его', 'но', 'да', 'ты', 'к', 'у', 'же', 'вы', 'за', 'бы', 'по',
            'только', 'ее', 'мне', 'было', 'вот', 'от', 'меня', 'еще', 'нет', 'о', 'из',
            'ему', 'теперь', 'когда', 'даже', 'ну', 'до', 'для', 'под', 'над', 'при', 'без',
            'шт', 'штук', 'штука', 'единица', 'единиц', 'упаковка', 'комплект', 'набор',
            'значение', 'характеристика', 'участник', 'закупки', 'заявка', 'производитель',
            'данных', 'наличие', 'отсутствие', 'должен', 'должна', 'должно', 'обязательно'
        }

        # Важные характеристики
        self.important_chars = {
            'цвет', 'размер', 'габариты', 'длина', 'ширина', 'высота', 'диаметр', 'толщина',
            'вес', 'масса', 'объем', 'материал', 'тип', 'вид', 'формат', 'модель', 'марка',
            'бренд', 'производитель', 'мощность', 'напряжение', 'память', 'процессор',
            'диагональ', 'разрешение', 'интерфейс', 'скорость', 'емкость', 'плотность'
        }

        # Базовые синонимы
        self.synonyms = {
            'красный': ['красная', 'красное', 'red'],
            'синий': ['синяя', 'синее', 'голубой', 'blue'],
            'черный': ['черная', 'черное', 'black'],
            'белый': ['белая', 'белое', 'white'],
            'зеленый': ['зеленая', 'зеленое', 'green'],
            'папка': ['скоросшиватель', 'folder', 'файл'],
            'ручка': ['авторучка', 'pen'],
            'карандаш': ['pencil', 'грифель'],
            'блок': ['блоки', 'стикеры', 'записи', 'заметки'],
            'компьютер': ['пк', 'pc', 'computer'],
            'ноутбук': ['лэптоп', 'laptop', 'notebook'],
            'монитор': ['дисплей', 'экран', 'display'],
            'клавиатура': ['keyboard', 'клавиши'],
            'мышь': ['mouse', 'манипулятор']
        }

        # Построим обратный словарь синонимов
        self.reverse_synonyms = {}
        for main_word, syns in self.synonyms.items():
            for syn in syns:
                if syn not in self.reverse_synonyms:
                    self.reverse_synonyms[syn] = set()
                self.reverse_synonyms[syn].add(main_word)
            # Добавляем само слово
            if main_word not in self.reverse_synonyms:
                self.reverse_synonyms[main_word] = set()
            self.reverse_synonyms[main_word].update(syns)

        # Настройки весов
        self.weight_config = {
            'name_terms': {'start': 4.0, 'step': 0.3, 'max': 5},
            'required_values': {'start': 3.5, 'step': 0.2, 'max': 5},
            'optional_values': {'start': 2.5, 'step': 0.2, 'max': 3},
            'char_names': {'start': 1.8, 'step': 0.2, 'max': 4},
            'synonym_penalty': 0.7,
            'min_weight': 1.0
        }

        self.logger.info("TenderTermExtractor инициализирован")

    def extract_from_tender_item(self, tender_item: Dict[str, Any]) -> Dict[str, Any]:
        """Извлечь термины из позиции тендера"""

        tender_name = tender_item.get('name', 'Без названия')
        self.logger.debug(f"Извлечение терминов из: {tender_name}")

        # 1. Извлекаем сырые термины
        raw_terms = self._extract_raw_terms(tender_item)

        # 2. Расширяем синонимами
        expanded_terms = self._expand_with_synonyms(raw_terms)

        # 3. Строим взвешенные термины
        result = self._build_weighted_terms(expanded_terms, tender_item, raw_terms)

        self.logger.debug(f"Извлечено терминов: {len(result['all_terms'])}, "
                          f"с весами: {len(result['weighted_terms'])}")

        return result

    def _extract_raw_terms(self, tender_item: Dict[str, Any]) -> Dict[str, List[str]]:
        """Извлечь сырые термины"""

        terms = {
            'name_terms': [],
            'char_names': [],
            'required_values': [],
            'optional_values': []
        }

        # Из названия
        name = tender_item.get('name', '')
        if name:
            terms['name_terms'] = self._clean_and_filter(name)
            self.logger.debug(f"Из названия: {terms['name_terms']}")

        # Из характеристик
        for char in tender_item.get('characteristics', []):
            char_name = char.get('name', '')
            char_value = str(char.get('value', ''))
            is_required = char.get('required', False)

            # Названия характеристик
            if char_name:
                char_name_terms = self._clean_and_filter(char_name)
                terms['char_names'].extend(char_name_terms)

            # Значения характеристик (не числовые диапазоны)
            if char_value and not self._is_numeric_range(char_value):
                value_terms = self._clean_and_filter(char_value)
                if is_required:
                    terms['required_values'].extend(value_terms)
                else:
                    terms['optional_values'].extend(value_terms)

        # Удаляем дубликаты
        for key in terms:
            terms[key] = list(dict.fromkeys(terms[key]))  # Сохраняем порядок

        return terms

    def _clean_and_filter(self, text: str) -> List[str]:
        """Очистить и отфильтровать текст"""

        if not text:
            return []

        # Приводим к нижнему регистру и убираем спецсимволы
        text = re.sub(r'[^\w\s]', ' ', text.lower())

        # Разбиваем на слова
        words = text.split()

        # Фильтруем
        filtered = []
        for word in words:
            # Пропускаем короткие, числа и стоп-слова
            if (len(word) > 2 and
                    not word.isdigit() and
                    word not in self.stop_words):
                filtered.append(word)

        return filtered

    def _is_numeric_range(self, value: str) -> bool:
        """Проверить, является ли значение числовым диапазоном"""

        indicators = ['≥', '≤', '>', '<', 'более', 'менее', 'от', 'до', 'свыше']
        return any(ind in value for ind in indicators)

    def _expand_with_synonyms(self, raw_terms: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """Расширить термины синонимами"""

        expanded = {}

        for category, terms in raw_terms.items():
            expanded[category] = list(terms)  # Копируем оригинальные

            # Добавляем синонимы
            for term in terms:
                if term in self.reverse_synonyms:
                    synonyms = self.reverse_synonyms[term]
                    for syn in synonyms:
                        if syn not in expanded[category]:
                            expanded[category].append(syn)

        return expanded

    def _build_weighted_terms(self, expanded_terms: Dict[str, List[str]],
                              tender_item: Dict[str, Any],
                              raw_terms: Dict[str, List[str]]) -> Dict[str, Any]:
        """Построить взвешенные термины"""

        result = {
            'search_query': '',
            'weighted_terms': {},
            'all_terms': [],
            'categories': {
                'name': [],
                'required': [],
                'optional': [],
                'characteristics': []
            }
        }

        # Основной поисковый запрос - из названия
        if expanded_terms['name_terms']:
            # Берем первые 2-3 оригинальных термина
            original_name_terms = raw_terms['name_terms'][:3]
            result['search_query'] = ' '.join(original_name_terms)
            result['categories']['name'] = expanded_terms['name_terms']

        # Применяем веса
        # 1. Термины из названия - максимальный вес
        config = self.weight_config['name_terms']
        for i, term in enumerate(expanded_terms['name_terms'][:config['max']]):
            weight = config['start'] - (i * config['step'])
            result['weighted_terms'][term] = max(weight, self.weight_config['min_weight'])

        # 2. Значения обязательных характеристик
        config = self.weight_config['required_values']
        for i, term in enumerate(expanded_terms['required_values'][:config['max']]):
            if term not in result['weighted_terms']:
                weight = config['start'] - (i * config['step'])
                result['weighted_terms'][term] = max(weight, self.weight_config['min_weight'])
        result['categories']['required'] = expanded_terms['required_values']

        # 3. Значения опциональных характеристик
        config = self.weight_config['optional_values']
        for i, term in enumerate(expanded_terms['optional_values'][:config['max']]):
            if term not in result['weighted_terms']:
                weight = config['start'] - (i * config['step'])
                result['weighted_terms'][term] = max(weight, self.weight_config['min_weight'])
        result['categories']['optional'] = expanded_terms['optional_values']

        # 4. Названия важных характеристик
        important_chars = [t for t in expanded_terms['char_names']
                           if any(imp in t for imp in self.important_chars)]
        config = self.weight_config['char_names']
        for i, term in enumerate(important_chars[:config['max']]):
            if term not in result['weighted_terms']:
                weight = config['start'] - (i * config['step'])
                result['weighted_terms'][term] = max(weight, self.weight_config['min_weight'])
        result['categories']['characteristics'] = expanded_terms['char_names']

        # Понижаем вес синонимов
        original_terms = set()
        for terms in raw_terms.values():
            original_terms.update(terms)

        for term, weight in list(result['weighted_terms'].items()):
            if term not in original_terms:
                # Это синоним - понижаем вес
                result['weighted_terms'][term] = weight * self.weight_config['synonym_penalty']

        # Убираем термины с низким весом
        result['weighted_terms'] = {
            term: weight
            for term, weight in result['weighted_terms'].items()
            if weight >= self.weight_config['min_weight']
        }

        # Все уникальные термины
        all_terms_set = set()
        for terms in expanded_terms.values():
            all_terms_set.update(terms)
        result['all_terms'] = list(all_terms_set)

        # Отладочная информация
        result['debug_info'] = {
            'tender_name': tender_item.get('name', ''),
            'total_characteristics': len(tender_item.get('characteristics', [])),
            'required_characteristics': sum(1 for c in tender_item.get('characteristics', [])
                                            if c.get('required', False)),
            'term_counts': {
                'name': len(expanded_terms['name_terms']),
                'required_values': len(expanded_terms['required_values']),
                'optional_values': len(expanded_terms['optional_values']),
                'char_names': len(expanded_terms['char_names'])
            },
            'weighted_terms_count': len(result['weighted_terms'])
        }

        self.logger.debug(f"Построено взвешенных терминов: {len(result['weighted_terms'])}")

        return result
