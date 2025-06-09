from fastapi import APIRouter, Depends, HTTPException, Body
from typing import Dict, Any, Union
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
        request_body: Dict[str, Any] = Body(...),  # Принимаем любой JSON
        tender_service=Depends(get_tender_matching_service),
        api_key: str = Depends(verify_api_key)
):
    """
    Обработать тендер и найти подходящие товары

    Принимает данные тендера и возвращает результат сопоставления
    с подходящими товарами и поставщиками из базы данных.

    Поддерживает два формата:
    1. Прямой формат: {"tenderInfo": {...}, "items": [...]}
    2. Обернутый формат: {"tender": {"tenderInfo": {...}, "items": [...]}}

    Parameters:
    - request_body: Данные тендера в одном из поддерживаемых форматов

    Returns:
    - TenderMatchingResult: Результат сопоставления с найденными товарами и поставщиками
    """
    try:
        # Извлекаем данные тендера если они обернуты
        if "tender" in request_body:
            tender_data = request_body["tender"]
            logger.info("Extracted tender data from wrapper")
        else:
            tender_data = request_body

        # Создаем объект TenderRequest
        try:
            tender_request = TenderRequest(**tender_data)
        except Exception as e:
            logger.error(f"Error parsing tender data: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tender data format: {str(e)}"
            )

        logger.info(f"Processing tender {tender_request.tenderInfo.tenderNumber}")

        if not tender_request.items:
            raise HTTPException(
                status_code=400,
                detail="Tender must contain at least one item"
            )

        # Фильтруем товары:
        # 1. Убираем товары без OKPD2 или с пустым OKPD2
        # 2. Убираем дубликаты по ID
        valid_items = []
        seen_ids = set()

        for item in tender_request.items:
            # Пропускаем если уже видели этот ID
            if item.id in seen_ids:
                logger.debug(f"Skipping duplicate item with id {item.id}")
                continue

            # Пропускаем если нет OKPD2 или он пустой
            if not item.okpd2Code or item.okpd2Code.strip() == "":
                logger.warning(f"Skipping item {item.id} '{item.name}' - empty OKPD2 code")
                continue

            seen_ids.add(item.id)
            valid_items.append(item)

        if not valid_items:
            raise HTTPException(
                status_code=400,
                detail="Tender must contain at least one item with valid OKPD2 code"
            )

        # Логируем статистику
        logger.info(
            f"Tender items: total={len(tender_request.items)}, "
            f"valid={len(valid_items)}, "
            f"skipped_no_okpd2={len([i for i in tender_request.items if not i.okpd2Code or i.okpd2Code.strip() == ''])}, "
            f"skipped_duplicates={len(tender_request.items) - len(seen_ids)}"
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