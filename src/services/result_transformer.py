from typing import Dict, Any, List, Optional
from datetime import datetime
import time

from src.models.tender import TenderMatchingResult, TenderRequest
from src.models.tender_v2 import (
    TenderMatchingResultV2, TenderItemMatchV2, MatchedProductV2, 
    MatchedSupplierV2, StandardizedAttributeV2, MatchDetailsV2,
    ProcessingStatsV2, ProcessingMetricsV2, SummaryV2,
    SupplierOfferV2, SupplierOfferPriceV2
)


class ResultTransformerCompatible:
    """Преобразователь результатов с сохранением совместимости имен полей"""
    
    @staticmethod
    def transform_to_v2(
        result: TenderMatchingResult, 
        tender_request: TenderRequest,
        processing_start_time: float
    ) -> TenderMatchingResultV2:
        """Преобразовать результат из формата v1 в формат v2 с сохранением имен полей"""
        
        # Извлекаем максимальную цену тендера
        tender_max_price = None
        if tender_request.tenderInfo and tender_request.tenderInfo.maxPrice:
            tender_max_price = tender_request.tenderInfo.maxPrice.get('amount')
        
        # Преобразуем результаты по позициям
        item_matches_v2 = []
        total_matched_products = 0
        total_supplier_offers = 0
        
        for item_match in result.item_matches:
            # Преобразуем товары
            matched_products = []
            
            for product in item_match.matched_products:
                # Преобразуем атрибуты
                standardized_attributes = []
                for attr in product.standardized_attributes:
                    standardized_attributes.append(StandardizedAttributeV2(
                        characteristic_type=attr.get('characteristic_type', attr.get('standard_name', '')),
                        standard_name=attr.get('standard_name', ''),
                        standard_value=attr.get('standard_value', ''),
                        unit=attr.get('unit')
                    ))
                
                # Преобразуем поставщиков
                matched_suppliers = []
                for supplier in product.matched_suppliers:
                    # Преобразуем предложения поставщика
                    supplier_offers = []
                    for offer in supplier.supplier_offers:
                        # Преобразуем цены
                        prices = []
                        if isinstance(offer, dict) and 'price' in offer:
                            for price_info in offer.get('price', []):
                                if isinstance(price_info, dict):
                                    prices.append(SupplierOfferPriceV2(
                                        qnt=price_info.get('qnt', 1),
                                        price=price_info.get('price', 0),
                                        discount=price_info.get('discount', 0)
                                    ))
                        
                        supplier_offer = SupplierOfferV2(
                            source_product_id=offer.get('source_product_id', ''),
                            collection_name=offer.get('collection_name', ''),
                            created_at=offer.get('created_at', datetime.utcnow().strftime('%d.%m.%Y %H:%M')),
                            purchase_url=offer.get('purchase_url'),
                            package_info=offer.get('package_info'),
                            stock=offer.get('stock'),
                            delivery_time=offer.get('delivery_time'),
                            price=prices
                        )
                        supplier_offers.append(supplier_offer)
                        total_supplier_offers += 1
                    
                    matched_supplier = MatchedSupplierV2(
                        supplier_name=supplier.supplier_name,
                        supplier_key=supplier.supplier_key,
                        supplier_address=supplier.supplier_address,  # Сохраняем как есть (может быть None)
                        supplier_tel=supplier.supplier_tel,          # Сохраняем как есть (может быть None)
                        match_score=supplier.match_score,
                        purchase_url=supplier.purchase_url,
                        matched_attributes=supplier.matched_attributes,  # Сохраняем для совместимости
                        supplier_offers=supplier_offers
                    )
                    matched_suppliers.append(matched_supplier)
                
                # Создаем детали соответствия с сохранением всех полей из v1
                match_details_dict = product.match_details if isinstance(product.match_details, dict) else {}
                
                match_details = MatchDetailsV2(
                    final_score=match_details_dict.get('final_score', product.match_score),
                    semantic_score=match_details_dict.get('semantic_score'),
                    text_score=match_details_dict.get('text_score'),
                    matched_attributes=match_details_dict.get('matched_attributes', []),
                    missing_attributes=match_details_dict.get('missing_attributes', []),
                    total_required=match_details_dict.get('total_required', 0),
                    total_matched=match_details_dict.get('total_matched', 0),
                    note=match_details_dict.get('note')
                )
                
                matched_product = MatchedProductV2(
                    product_hash=product.product_hash,
                    sample_brand=product.sample_brand,
                    sample_title=product.sample_title,
                    okpd2_code=product.okpd2_code,
                    okpd2_name=product.okpd2_name,
                    match_score=product.match_score,
                    total_suppliers=product.total_suppliers,
                    match_details=match_details,
                    standardized_attributes=standardized_attributes,
                    matched_suppliers=matched_suppliers
                )
                matched_products.append(matched_product)
                total_matched_products += 1
            
            # Преобразуем статистику обработки
            processing_stats = None
            if item_match.processing_stats:
                processing_stats = ProcessingStatsV2(**item_match.processing_stats)
            
            item_match_v2 = TenderItemMatchV2(
                tender_item_id=item_match.tender_item_id,
                tender_item_name=item_match.tender_item_name,
                okpd2_code=item_match.okpd2_code,
                processing_status=item_match.processing_status,
                best_match_score=item_match.best_match_score,
                total_matches=item_match.total_matches,
                error_message=item_match.error_message,
                matched_products=matched_products,
                processing_stats=processing_stats
            )
            item_matches_v2.append(item_match_v2)
        
        # Рассчитываем общее время обработки
        total_processing_time = time.time() - processing_start_time
        
        # Создаем метрики обработки
        processing_metrics = ProcessingMetricsV2(
            classifier_time=None,  # Не используется в текущей версии
            matcher_time=result.summary.get('processing_duration_seconds', total_processing_time),
            standardizer_time=None,  # Не используется в текущей версии
            total_time=total_processing_time
        )
        
        # Обновляем итоговую статистику
        summary_dict = result.summary.copy()
        summary_dict['total_matched_products'] = total_matched_products
        summary_dict['total_supplier_offers'] = total_supplier_offers
        
        # Добавляем отсутствующие поля если их нет
        if 'items_per_second' not in summary_dict:
            duration = summary_dict.get('processing_duration_seconds', 1)
            summary_dict['items_per_second'] = result.total_items / duration if duration > 0 else 0
        
        if 'parallel_batch_size' not in summary_dict:
            summary_dict['parallel_batch_size'] = None
        
        # Добавляем значения по умолчанию для новых полей
        if 'items_with_errors' not in summary_dict:
            summary_dict['items_with_errors'] = 0
            
        summary = SummaryV2(**summary_dict)
        
        return TenderMatchingResultV2(
            # Сохраняем все поля из v1 с теми же именами
            tender_name=result.tender_name,
            tender_number=result.tender_number,
            processing_time=result.processing_time,  # Сохраняем имя из v1
            total_items=result.total_items,
            matched_items=result.matched_items,
            item_matches=item_matches_v2,  # Сохраняем имя из v1
            summary=summary,
            
            # Новые поля v2
            tender_max_price=tender_max_price,
            processing_metrics=processing_metrics,
            created_at=datetime.utcnow()  # Дополнительное поле
        )