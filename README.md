# Tender Matching Service

Сервис для сопоставления товаров из тендеров с товарами из базы данных уникальных товаров.

## Описание

Сервис принимает данные о тендере (список товаров с характеристиками) и находит в базе данных подходящие товары с их поставщиками. Для каждого товара из тендера подбираются наиболее подходящие варианты на основе:
- OKPD2 кода
- Стандартизированных характеристик  
- Ценовых параметров

## Структура проекта

```
tender_matching_service/
├── src/
│   ├── api/
│   │   ├── dependencies.py      # Проверка API ключей
│   │   └── endpoints/
│   │       └── tender_matching.py  # API endpoints
│   ├── core/
│   │   ├── config.py           # Конфигурация
│   │   ├── exceptions.py       # Исключения
│   │   └── logging_config.py   # Настройка логирования
│   ├── models/
│   │   └── tender.py           # Модели данных
│   ├── services/
│   │   └── tender_matcher.py   # Бизнес-логика сопоставления
│   ├── storage/
│   │   └── unique_products_mongo.py  # Работа с БД товаров
│   └── main.py                 # Точка входа FastAPI
├── examples/
│   └── test_tender.py          # Пример использования
├── requirements.txt
├── .env.example
├── docker-compose.yml
├── Dockerfile
└── README.md
```

## Установка

1. Клонировать репозиторий:
```bash
git clone <repository-url>
cd tender_matching_service
```

2. Создать виртуальное окружение:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate  # Windows
```

3. Установить зависимости:
```bash
pip install -r requirements.txt
```

4. Настроить переменные окружения:
```bash
cp .env.example .env
# Отредактировать .env файл
```

## Конфигурация

Основные параметры в `.env`:

```env
# API (опционально, если не указан - проверка отключена)
API_KEY=your-secret-api-key

# MongoDB с уникальными товарами
UNIQUE_MONGO_HOST=localhost
UNIQUE_MONGO_PORT=27017
UNIQUE_MONGO_USER=
UNIQUE_MONGO_PASS=
UNIQUE_MONGODB_DATABASE=unique_products
UNIQUE_COLLECTION_NAME=unique_products

# Параметры обработки
MIN_MATCH_SCORE=0.5
MAX_MATCHED_PRODUCTS_PER_ITEM=10
PRICE_TOLERANCE_PERCENT=20.0
```

## Запуск

### Development:
```bash
uvicorn src.main:app --reload --port 8002
```

### Production:
```bash
uvicorn src.main:app --host 0.0.0.0 --port 8002 --workers 4
```

### Docker:
```bash
docker-compose up -d
```

## API Endpoints

### 1. Обработка тендера

```http
POST /api/v1/tenders/match
Content-Type: application/json
X-API-Key: {api_key}  # Если настроен

{
  "tenderInfo": {
    "tenderName": "Поставка канцтоваров",
    "tenderNumber": "0348100021725000059",
    "customerName": "УПРАВЛЕНИЕ СУДЕБНОГО ДЕПАРТАМЕНТА",
    "purchaseType": "Электронный аукцион",
    "financingSource": "Бюджетные средства",
    "maxPrice": {
      "amount": 1927798.48,
      "currency": "RUB"
    },
    "deliveryInfo": {...},
    "paymentInfo": {...}
  },
  "items": [
    {
      "id": 1,
      "name": "Клейкая лента",
      "okpd2Code": "22.29.21.000",
      "ktruCode": "22.29.21.000-00000002",
      "quantity": 800,
      "unitOfMeasurement": "Штука",
      "unitPrice": {"amount": 133.22, "currency": "RUB"},
      "totalPrice": {"amount": 106576, "currency": "RUB"},
      "characteristics": [
        {
          "id": 1,
          "name": "Ширина клейкой ленты",
          "value": "≥ 50",
          "unit": "Миллиметр",
          "type": "Количественная",
          "required": true
        }
      ]
    }
  ]
}
```

Ответ:
```json
{
  "tender_number": "0348100021725000059",
  "tender_name": "Поставка канцтоваров",
  "processing_time": "2024-12-10T10:30:00Z",
  "total_items": 1,
  "matched_items": 1,
  "item_matches": [
    {
      "tender_item_id": 1,
      "tender_item_name": "Клейкая лента",
      "okpd2_code": "22.29.21.000",
      "matched_products": [
        {
          "product_hash": "1a401762...",
          "okpd2_code": "22.29.21.000",
          "sample_title": "Клейкая лента упаковочная",
          "sample_brand": "BRAUBERG",
          "match_score": 0.85,
          "matched_suppliers": [
            {
              "supplier_name": "ООО Канцтовары",
              "supplier_tel": "8 (495) 123-45-67",
              "purchase_url": "https://example.com/product/123",
              "match_score": 0.9
            }
          ]
        }
      ],
      "total_matches": 5,
      "best_match_score": 0.85
    }
  ],
  "summary": {
    "total_suppliers": 15,
    "average_match_score": 0.75,
    "processing_duration_seconds": 1.23
  }
}
```

### 2. Статус сервиса

```http
GET /api/v1/tenders/status
X-API-Key: {api_key}
```

### 3. Проверка здоровья

```http
GET /health
```

## Алгоритм сопоставления

1. **Фильтрация по OKPD2**: Находим товары с совпадающим кодом OKPD2
2. **Сопоставление характеристик**:
   - Нормализация названий характеристик
   - Сравнение значений (для количественных - с учетом условий ≥, ≤, диапазонов)
   - Расчет score на основе процента совпадений обязательных характеристик
3. **Оценка поставщиков**:
   - Проверка ценовых предложений
   - Бонусы за более низкую цену
4. **Ранжирование**: Сортировка по match_score

### Поддерживаемые операторы сравнения:
- `≥ X` - больше или равно
- `≤ X` - меньше или равно  
- `> X` - больше
- `< X` - меньше
- `≥ X и < Y` - диапазон [X, Y)
- `> X и ≤ Y` - диапазон (X, Y]
- `X` - точное значение

## Примеры использования

### Python:
```python
import httpx

# Подготовка данных
tender_data = {
    "tenderInfo": {...},
    "items": [...]
}

# Отправка запроса
response = httpx.post(
    "http://localhost:8002/api/v1/tenders/match",
    json=tender_data,
    headers={"X-API-Key": "your-api-key"}  # Если настроен
)

result = response.json()
print(f"Найдено {result['matched_items']} из {result['total_items']} товаров")
```

### curl:
```bash
curl -X POST http://localhost:8002/api/v1/tenders/match \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d @tender.json
```

## Мониторинг

Сервис записывает логи в:
- stdout (консоль)
- `logs/tender_matching_YYYYMMDD.log` (файлы с ротацией)


## Производительность

- Рекомендуемый лимит: 100 товаров на тендер
- Среднее время обработки: 10-50 мс на товар
- Максимальная нагрузка: зависит от MongoDB

## Требования

- Python 3.11+
- MongoDB 4.4+
- 512MB RAM минимум
- 2 CPU рекомендуется