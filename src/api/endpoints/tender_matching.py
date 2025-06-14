from fastapi import APIRouter, Depends, HTTPException, Body, Query
from typing import Dict, Any, Union, Optional, List
import logging

from src.api.dependencies import verify_api_key
from src.storage.unique_products_mongo import UniqueProductsMongoStore
from src.services.tender_matcher import TenderMatchingService
from src.models.tender import TenderRequest, TenderMatchingResult, TenderItem, TenderItemMatch
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
        use_semantic: Optional[bool] = Query(default=None, description="Использовать семантический поиск"),
        semantic_threshold: Optional[float] = Query(default=None, ge=0.0, le=1.0,
                                                    description="Порог семантической схожести"),
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
    - use_semantic: Включить семантический поиск (если не указано - берется из конфигурации)
    - semantic_threshold: Минимальная семантическая схожесть (0.0-1.0)

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


@router.post("/match-item", response_model=TenderItemMatch)
async def match_single_item(
        item_data: Dict[str, Any] = Body(..., example={
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
                    "required": True
                }
            ]
        }),
        use_semantic: Optional[bool] = Query(default=None, description="Использовать семантический поиск"),
        semantic_threshold: Optional[float] = Query(default=None, ge=0.0, le=1.0,
                                                    description="Порог семантической схожести"),
        max_results: Optional[int] = Query(default=None, ge=1, le=50,
                                           description="Максимум результатов (по умолчанию из конфигурации)"),
        tender_service=Depends(get_tender_matching_service),
        api_key: str = Depends(verify_api_key)
):
    """
    Найти подходящие товары для одной позиции тендера

    Упрощенный эндпоинт для тестирования сопоставления отдельного товара
    без необходимости передавать полную структуру тендера.

    Parameters:
    - item_data: Данные товара в формате TenderItem
    - use_semantic: Включить семантический поиск
    - semantic_threshold: Минимальная семантическая схожесть (0.0-1.0)
    - max_results: Максимальное количество результатов

    Returns:
    - TenderItemMatch: Результат сопоставления с найденными товарами
    """
    try:
        # Валидация и создание TenderItem
        try:
            # Добавляем значения по умолчанию если отсутствуют
            if 'id' not in item_data:
                item_data['id'] = 1
            if 'quantity' not in item_data:
                item_data['quantity'] = 1
            if 'unitOfMeasurement' not in item_data:
                item_data['unitOfMeasurement'] = "Штука"
            if 'unitPrice' not in item_data:
                item_data['unitPrice'] = {"amount": 0, "currency": "RUB"}
            if 'totalPrice' not in item_data:
                item_data['totalPrice'] = {"amount": 0, "currency": "RUB"}
            if 'characteristics' not in item_data:
                item_data['characteristics'] = []

            tender_item = TenderItem(**item_data)
        except Exception as e:
            logger.error(f"Error parsing item data: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid item data format: {str(e)}"
            )

        # Проверка OKPD2
        if not tender_item.okpd2Code or tender_item.okpd2Code.strip() == "":
            raise HTTPException(
                status_code=400,
                detail="Item must have a valid OKPD2 code"
            )

        logger.info(f"Processing single item: {tender_item.name} (OKPD2: {tender_item.okpd2Code})")

        # Настраиваем параметры поиска
        if use_semantic is not None:
            tender_service.enable_semantic_search = use_semantic

        if semantic_threshold is not None:
            tender_service.semantic_threshold = semantic_threshold

        # Временно изменяем max_results если указан
        original_max_results = settings.max_matched_products_per_item
        if max_results is not None:
            settings.max_matched_products_per_item = max_results

        try:
            # Выполняем поиск
            result = await tender_service.match_tender_item(tender_item)

            # Добавляем дополнительную информацию
            if hasattr(result, 'processing_stats') and result.processing_stats:
                result.processing_stats['search_params'] = {
                    'semantic_search': use_semantic if use_semantic is not None else tender_service.enable_semantic_search,
                    'semantic_threshold': semantic_threshold if semantic_threshold is not None else tender_service.semantic_threshold,
                    'max_results': max_results if max_results is not None else original_max_results
                }

            logger.info(
                f"Item processed successfully. Found {result.total_matches} matches, "
                f"best score: {result.best_match_score:.2f}"
            )

            return result

        finally:
            # Восстанавливаем оригинальное значение
            settings.max_matched_products_per_item = original_max_results

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing item: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/match-items", response_model=List[TenderItemMatch])
async def match_multiple_items(
        items_data: List[Dict[str, Any]] = Body(..., example=[
            {
                "id": 1,
                "name": "Клейкая лента",
                "okpd2Code": "22.29.21.000",
                "quantity": 800,
                "unitOfMeasurement": "Штука",
                "unitPrice": {"amount": 133.22, "currency": "RUB"},
                "characteristics": []
            },
            {
                "id": 2,
                "name": "Ручка шариковая",
                "okpd2Code": "32.99.12.110",
                "quantity": 500,
                "unitOfMeasurement": "Штука",
                "unitPrice": {"amount": 15.50, "currency": "RUB"},
                "characteristics": [
                    {
                        "name": "Цвет чернил",
                        "value": "синий",
                        "type": "Качественная",
                        "required": True
                    }
                ]
            }
        ]),
        use_semantic: Optional[bool] = Query(default=None, description="Использовать семантический поиск"),
        semantic_threshold: Optional[float] = Query(default=None, ge=0.0, le=1.0,
                                                    description="Порог семантической схожести"),
        max_results_per_item: Optional[int] = Query(default=None, ge=1, le=50,
                                                    description="Максимум результатов на товар"),
        tender_service=Depends(get_tender_matching_service),
        api_key: str = Depends(verify_api_key)
):
    """
    Найти подходящие товары для нескольких позиций

    Позволяет протестировать сопоставление нескольких товаров одновременно
    без создания полной структуры тендера.

    Parameters:
    - items_data: Список товаров
    - use_semantic: Включить семантический поиск
    - semantic_threshold: Минимальная семантическая схожесть
    - max_results_per_item: Максимум результатов на каждый товар

    Returns:
    - List[TenderItemMatch]: Результаты сопоставления для каждого товара
    """
    try:
        if not items_data:
            raise HTTPException(
                status_code=400,
                detail="At least one item is required"
            )

        if len(items_data) > 20:
            raise HTTPException(
                status_code=400,
                detail="Maximum 20 items allowed per request"
            )

        # Настраиваем параметры
        if use_semantic is not None:
            tender_service.enable_semantic_search = use_semantic

        if semantic_threshold is not None:
            tender_service.semantic_threshold = semantic_threshold

        original_max_results = settings.max_matched_products_per_item
        if max_results_per_item is not None:
            settings.max_matched_products_per_item = max_results_per_item

        try:
            results = []

            for idx, item_data in enumerate(items_data):
                try:
                    # Добавляем значения по умолчанию
                    if 'id' not in item_data:
                        item_data['id'] = idx + 1
                    if 'quantity' not in item_data:
                        item_data['quantity'] = 1
                    if 'unitOfMeasurement' not in item_data:
                        item_data['unitOfMeasurement'] = "Штука"
                    if 'unitPrice' not in item_data:
                        item_data['unitPrice'] = {"amount": 0, "currency": "RUB"}
                    if 'totalPrice' not in item_data:
                        item_data['totalPrice'] = {"amount": 0, "currency": "RUB"}
                    if 'characteristics' not in item_data:
                        item_data['characteristics'] = []

                    tender_item = TenderItem(**item_data)

                    # Проверяем OKPD2
                    if not tender_item.okpd2Code or tender_item.okpd2Code.strip() == "":
                        logger.warning(f"Skipping item {tender_item.id} without OKPD2")
                        continue

                    # Обрабатываем товар
                    result = await tender_service.match_tender_item(tender_item)
                    results.append(result)

                except Exception as e:
                    logger.error(f"Error processing item {idx + 1}: {e}")
                    # Создаем результат с ошибкой
                    error_result = TenderItemMatch(
                        tender_item_id=item_data.get('id', idx + 1),
                        tender_item_name=item_data.get('name', 'Unknown'),
                        okpd2_code=item_data.get('okpd2Code', ''),
                        matched_products=[],
                        total_matches=0,
                        best_match_score=0.0,
                        processing_status="error",
                        error_message=str(e)
                    )
                    results.append(error_result)

            return results

        finally:
            settings.max_matched_products_per_item = original_max_results

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing items: {e}", exc_info=True)
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

        # Проверяем доступность семантического поиска
        semantic_available = False
        try:
            from src.services.semantic_search import SemanticSearchService
            semantic_available = True
        except:
            pass

        return {
            "service": "Tender Matching Service",
            "status": "operational",
            "version": "1.2.0",  # Обновленная версия
            "features": {
                "semantic_search_available": semantic_available,
                "semantic_search_enabled": getattr(settings, 'enable_semantic_search', False),
                "text_index_available": stats.get("text_index_available", False)
            },
            "database": {
                "total_unique_products": stats.get("total_unique_products", 0),
                "by_okpd_class": stats.get("by_okpd_class", {}),
                "deduplication_rate": stats.get("deduplication_rate", 0)
            },
            "configuration": {
                "min_match_score": settings.min_match_score,
                "max_matched_products_per_item": settings.max_matched_products_per_item,
                "price_tolerance_percent": settings.price_tolerance_percent,
                "semantic_threshold": getattr(settings, 'semantic_threshold', 0.35)
            }
        }
    except Exception as e:
        logger.error(f"Error getting service status: {e}")
        return {
            "service": "Tender Matching Service",
            "status": "degraded",
            "error": str(e)
        }


@router.post("/analyze-item")
async def analyze_tender_item(
        item_data: Dict[str, Any] = Body(...),
        unique_products_store=Depends(get_unique_products_store),
        api_key: str = Depends(verify_api_key)
) -> Dict[str, Any]:
    """
    Анализировать одну позицию тендера (для отладки)

    Показывает извлеченные термины и процесс поиска.
    """
    try:
        # Проверяем доступность экстрактора терминов
        try:
            from src.services.term_extractor import TenderTermExtractor
            extractor = TenderTermExtractor()

            # Извлекаем термины
            terms = extractor.extract_from_tender_item(item_data)

            # Ищем товары с расширенным поиском
            products = await unique_products_store.find_products_enhanced(
                okpd2_code=item_data.get('okpd2Code'),
                search_terms=terms.get('all_terms', []),
                weighted_terms=terms.get('weighted_terms', {}),
                limit=10
            )

            extracted_info = {
                "search_query": terms.get('search_query'),
                "weighted_terms": terms.get('weighted_terms'),
                "categories": terms.get('categories'),
                "total_terms": len(terms.get('all_terms', []))
            }
        except:
            # Fallback если экстрактор недоступен
            products = await unique_products_store.find_products(
                filters={"okpd2_code": {"$regex": f"^{item_data.get('okpd2Code', '')}"}},
                limit=10
            )
            extracted_info = {"error": "Term extractor not available"}

        return {
            "item_name": item_data.get('name'),
            "okpd2_code": item_data.get('okpd2Code'),
            "extracted_terms": extracted_info,
            "found_products": len(products),
            "sample_products": [
                {
                    "title": p.get('sample_title'),
                    "brand": p.get('sample_brand'),
                    "okpd2": p.get('okpd2_code'),
                    "text_score": p.get('text_search_score', 0),
                    "weighted_score": p.get('weighted_score', 0)
                }
                for p in products[:3]
            ]
        }

    except Exception as e:
        logger.error(f"Ошибка анализа позиции: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))