import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.endpoints import tender_matching
from src.core.config import settings
from src.core.logging_config import setup_app_logging

# Настройка логирования
setup_app_logging(service_name="tender_matching", level="INFO")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    # Startup
    logger.info("Starting tender matching service...")
    logger.info(f"Service port: {settings.service_port}")
    logger.info(f"Min match score: {settings.min_match_score}")
    logger.info(f"Max products per item: {settings.max_matched_products_per_item}")
    logger.info(f"Semantic search: {'Enabled' if settings.enable_semantic_search else 'Disabled'}")

    yield

    # Shutdown
    logger.info("Shutting down tender matching service...")


# Создание приложения
app = FastAPI(
    title="Tender Matching Service",
    description="""
    Сервис сопоставления товаров из тендеров с товарами из базы данных.

    ## Возможности

    * **Полная обработка тендеров** - обработка всего тендера с множеством товаров
    * **Тестирование одного товара** - быстрая проверка без создания полного JSON тендера
    * **Пакетная обработка** - обработка до 20 товаров одновременно
    * **Семантический поиск** - улучшенное сопоставление с использованием ML
    * **Анализ товаров** - просмотр извлеченных терминов и процесса поиска

    ## Алгоритмы сопоставления

    1. **Стандартный**: OKPD2 → Характеристики → Поставщики
    2. **Улучшенный**: Извлечение терминов → Семантический поиск → Точное сопоставление
    """,
    version="1.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключение роутеров
app.include_router(
    tender_matching.router,
    prefix="/api/v1/tenders",
    tags=["tender_matching"]
)


# Health check
@app.get("/health", tags=["monitoring"])
async def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy",
        "service": "Tender Matching Service",
        "version": "1.2.0"
    }


# Root endpoint
@app.get("/", tags=["info"])
async def root():
    """Корневой эндпоинт с информацией о сервисе"""
    return {
        "service": "Tender Matching Service",
        "version": "1.2.0",
        "docs": "/docs",
        "description": "Сервис сопоставления товаров из тендеров с товарами из базы данных",
        "features": [
            "Обработка полных тендеров",
            "Тестирование отдельных товаров",
            "Пакетная обработка",
            "Семантический поиск (ML)",
            "Интеллектуальное сопоставление характеристик"
        ],
        "endpoints": {
            "match_tender": {
                "method": "POST",
                "path": "/api/v1/tenders/match",
                "description": "Обработать полный тендер"
            },
            "match_single_item": {
                "method": "POST",
                "path": "/api/v1/tenders/match-item",
                "description": "Тестировать один товар (NEW!)",
                "params": ["use_semantic", "semantic_threshold", "max_results"]
            },
            "match_multiple_items": {
                "method": "POST",
                "path": "/api/v1/tenders/match-items",
                "description": "Обработать несколько товаров (NEW!)",
                "params": ["use_semantic", "semantic_threshold", "max_results_per_item"]
            },
            "analyze_item": {
                "method": "POST",
                "path": "/api/v1/tenders/analyze-item",
                "description": "Анализировать товар (отладка)"
            },
            "service_status": {
                "method": "GET",
                "path": "/api/v1/tenders/status",
                "description": "Статус сервиса и статистика"
            },
            "health": {
                "method": "GET",
                "path": "/health",
                "description": "Проверка здоровья"
            }
        },
        "quick_start": {
            "minimal_request": {
                "endpoint": "POST /api/v1/tenders/match-item",
                "body": {
                    "name": "Ручка шариковая",
                    "okpd2Code": "32.99.12.110"
                }
            },
            "with_characteristics": {
                "endpoint": "POST /api/v1/tenders/match-item?use_semantic=true",
                "body": {
                    "name": "Бумага офисная А4",
                    "okpd2Code": "17.12.14.110",
                    "characteristics": [
                        {
                            "name": "Плотность",
                            "value": "80",
                            "unit": "г/м2",
                            "type": "Количественная",
                            "required": True
                        }
                    ]
                }
            }
        }
    }