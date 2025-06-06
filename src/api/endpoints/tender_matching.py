from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
import logging

from src.api.dependencies import verify_api_key
from src.storage.unique_products_mongo import UniqueProductsMongoStore
from src.services.tender_matcher import TenderMatchingService
from src.models.tender import TenderRequest, TenderMatchingResult
from src.core.config import settings
from src.core.exceptions import TenderProcessingException

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_unique_products_store() -> UniqueProductsMongoStore:
    """Получить экземпляр UniqueProductsMongoStore"""
    store = UniqueProductsMongoStore(
        settings.unique_mongodb_database,
        settings.unique_collection_name
    )
    await store.initialize()
    return store


async def get_tender_matching_service(
        unique_products_store=Depends(get_unique_products_store)
) -> TenderMatchingService:
    """Получить экземпляр TenderMatchingService"""
    return TenderMatchingService(unique_products_store)


@router.post("/match", response_model=TenderMatchingResult)
async def match_tender(
        tender_request: TenderRequest,
        tender_service=Depends(get_tender_matching_service),
        api_key: str = Depends(verify_api_key)
):
    """
    Обработать тендер и найти подходящие товары

    Принимает данные тендера и возвращает результат сопоставления
    с подходящими товарами и поставщиками из базы данных.

    Parameters:
    - tender_request: Данные тендера включая информацию о тендере и список товаров

    Returns:
    - TenderMatchingResult: Результат сопоставления с найденными товарами и поставщиками
    """
    try:
        logger.info(f"Processing tender {tender_request.tenderInfo.tenderNumber}")

        # Валидация входных данных
        if not tender_request.items:
            raise HTTPException(
                status_code=400,
                detail="Tender must contain at least one item"
            )

        # Фильтруем товары с нулевым количеством
        valid_items = [item for item in tender_request.items if item.quantity > 0]

        if not valid_items:
            raise HTTPException(
                status_code=400,
                detail="Tender must contain at least one item with quantity > 0"
            )

        # Обновляем запрос только валидными товарами
        tender_request.items = valid_items

        # Обрабатываем тендер
        result = await tender_service.process_tender(tender_request)

        logger.info(
            f"Tender {tender_request.tenderInfo.tenderNumber} processed successfully. "
            f"Matched {result.matched_items}/{result.total_items} items"
        )

        return result

    except HTTPException:
        # Пробрасываем HTTPException дальше
        raise
    except TenderProcessingException as e:
        logger.error(f"Error processing tender: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error processing tender: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/status")
async def get_service_status(
        unique_products_store=Depends(get_unique_products_store),
        api_key: str = Depends(verify_api_key)
) -> Dict[str, Any]:
    """
    Получить статус сервиса и статистику по базе товаров

    Returns:
    - Информация о состоянии сервиса и количестве товаров в базе
    """
    try:
        # Получаем статистику из БД
        stats = await unique_products_store.get_statistics()

        return {
            "service": "Tender Matching Service",
            "status": "operational",
            "database": {
                "total_unique_products": stats.get("total_unique_products", 0),
                "by_okpd_class": stats.get("by_okpd_class", {}),
                "deduplication_rate": stats.get("deduplication_rate", 0)
            },
            "configuration": {
                "min_match_score": settings.min_match_score,
                "max_matched_products_per_item": settings.max_matched_products_per_item,
                "price_tolerance_percent": settings.price_tolerance_percent
            }
        }
    except Exception as e:
        logger.error(f"Error getting service status: {e}")
        return {
            "service": "Tender Matching Service",
            "status": "degraded",
            "error": str(e)
        }