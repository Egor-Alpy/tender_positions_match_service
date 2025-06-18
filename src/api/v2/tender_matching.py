from fastapi import APIRouter, Depends, HTTPException, Body, Query
from typing import Dict, Any, Optional
import logging
import time

from src.api.dependencies import verify_api_key
from src.storage.unique_products_mongo import UniqueProductsMongoStore
from src.services.tender_matcher import TenderMatchingService
from src.services.result_transformer import ResultTransformerCompatible
from src.models.tender import TenderRequest
from src.models.tender_v2 import TenderMatchingResultV2
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


@router.post("/match", response_model=TenderMatchingResultV2)
async def match_tender_v2(
        request_body: Dict[str, Any] = Body(...),  # Принимаем любой JSON
        use_semantic: Optional[bool] = Query(default=None, description="Использовать семантический поиск"),
        semantic_threshold: Optional[float] = Query(default=None, ge=0.0, le=1.0,
                                                    description="Порог семантической схожести"),
        tender_service=Depends(get_tender_matching_service),
        api_key: str = Depends(verify_api_key)
):
    """
    API v2: Обработать тендер и найти подходящие товары

    Принимает данные тендера и возвращает результат сопоставления
    с подходящими товарами и поставщиками из базы данных в расширенном формате v2.

    ## Совместимость с v1
    API v2 полностью совместима с клиентами v1. Все поля из v1 сохранены с теми же именами:
    - `item_matches` (не переименовано)
    - `processing_time` (не переименовано)
    - Все поля поставщиков остались Optional

    ## Новые возможности v2:
    - `tender_max_price` - максимальная цена тендера
    - `processing_metrics` - детальные метрики времени обработки
    - `supplier_offers` - структурированная информация о предложениях поставщиков
    - Расширенная статистика в `summary`
    - Типизированные модели вместо словарей

    Parameters:
    - request_body: Данные тендера (формат запроса идентичен v1)
    - use_semantic: Включить семантический поиск
    - semantic_threshold: Минимальная семантическая схожесть (0.0-1.0)

    Returns:
    - TenderMatchingResultV2: Результат в формате v2 (обратно совместим с v1)
    """
    processing_start_time = time.time()
    
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

        logger.info(f"Processing tender {tender_request.tenderInfo.tenderNumber} (API v2)")

        if not tender_request.items:
            raise HTTPException(
                status_code=400,
                detail="Tender must contain at least one item"
            )

        # Настраиваем параметры семантического поиска если переданы
        if use_semantic is not None:
            tender_service.enable_semantic_search = use_semantic
            logger.info(f"Семантический поиск {'включен' if use_semantic else 'отключен'} через параметр запроса")

        if semantic_threshold is not None:
            tender_service.semantic_threshold = semantic_threshold
            logger.info(f"Порог семантической схожести установлен: {semantic_threshold}")

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
        result_v1 = await tender_service.process_tender(tender_request)

        # Преобразуем результат в формат v2
        result_v2 = ResultTransformerCompatible.transform_to_v2(
            result_v1, 
            tender_request,
            processing_start_time
        )

        logger.info(
            f"Tender {tender_request.tenderInfo.tenderNumber} processed successfully (API v2). "
            f"Matched {result_v2.matched_items}/{result_v2.total_items} items"
        )

        return result_v2

    except HTTPException:
        # Пробрасываем HTTPException дальше
        raise
    except TenderProcessingException as e:
        logger.error(f"Error processing tender: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error processing tender: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")