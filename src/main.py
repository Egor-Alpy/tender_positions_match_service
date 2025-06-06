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

    yield

    # Shutdown
    logger.info("Shutting down tender matching service...")


# Создание приложения
app = FastAPI(
    title="Tender Matching Service",
    description="Сервис сопоставления товаров из тендеров с товарами из базы данных",
    version="1.0.0",
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
        "version": "1.0.0"
    }


# Root endpoint
@app.get("/", tags=["info"])
async def root():
    """Корневой эндпоинт с информацией о сервисе"""
    return {
        "service": "Tender Matching Service",
        "version": "1.0.0",
        "docs": "/docs",
        "description": "Сервис сопоставления товаров из тендеров с товарами из базы данных",
        "endpoints": {
            "match_tender": {
                "method": "POST",
                "path": "/api/v1/tenders/match",
                "description": "Обработать тендер и найти подходящие товары"
            },
            "service_status": {
                "method": "GET",
                "path": "/api/v1/tenders/status",
                "description": "Получить статус сервиса и статистику"
            },
            "health": {
                "method": "GET",
                "path": "/health",
                "description": "Проверка здоровья сервиса"
            }
        }
    }