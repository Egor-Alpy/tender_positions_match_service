from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any, Optional


class SupplierOfferPriceV2(BaseModel):
    """Ценовое предложение поставщика"""
    qnt: int = Field(..., description="Минимальное количество")
    price: float = Field(..., description="Цена за единицу")
    discount: float = Field(0, description="Скидка в %")


class SupplierOfferV2(BaseModel):
    """Предложение поставщика"""
    source_product_id: str
    collection_name: str
    created_at: str
    purchase_url: Optional[str] = None
    package_info: Optional[str] = None
    stock: Optional[str] = None
    delivery_time: Optional[str] = None
    price: List[SupplierOfferPriceV2] = Field(default_factory=list)


class MatchedSupplierV2(BaseModel):
    """Поставщик в формате v2"""
    supplier_name: str
    supplier_key: str
    supplier_address: Optional[str] = None  # Сохраняем как Optional для совместимости
    supplier_tel: Optional[str] = None      # Сохраняем как Optional для совместимости
    match_score: float
    purchase_url: Optional[str] = None
    matched_attributes: List[str] = Field(default_factory=list)  # Сохраняем для совместимости
    supplier_offers: List[SupplierOfferV2] = Field(default_factory=list)


class StandardizedAttributeV2(BaseModel):
    """Стандартизированная характеристика товара"""
    characteristic_type: str
    standard_name: str
    standard_value: str
    unit: Optional[str] = None


class MatchDetailsV2(BaseModel):
    """Детали соответствия"""
    final_score: float
    semantic_score: Optional[float] = None
    text_score: Optional[float] = None
    matched_attributes: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    missing_attributes: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    total_required: Optional[int] = 0
    total_matched: Optional[int] = 0
    note: Optional[str] = None


class MatchedProductV2(BaseModel):
    """Товар в формате v2 - сохраняем все поля из v1"""
    product_hash: str
    sample_brand: Optional[str] = None
    sample_title: Optional[str] = None
    okpd2_code: str
    okpd2_name: str
    match_score: float
    total_suppliers: int
    match_details: MatchDetailsV2
    standardized_attributes: List[StandardizedAttributeV2] = Field(default_factory=list)
    matched_suppliers: List[MatchedSupplierV2] = Field(default_factory=list)


class ProcessingStatsV2(BaseModel):
    """Статистика обработки позиции"""
    search_query: Optional[str] = None
    candidates_found: Optional[int] = None
    after_semantic_filter: Optional[int] = None
    matched_products: Optional[int] = None
    processing_time: Optional[float] = None
    weighted_terms_count: Optional[int] = None
    search_params: Optional[Dict[str, Any]] = None


class TenderItemMatchV2(BaseModel):
    """Результат обработки позиции тендера - сохраняем имя из v1"""
    tender_item_id: Optional[int] = None
    tender_item_name: Optional[str] = None
    okpd2_code: Optional[str] = None
    processing_status: str = "success"
    best_match_score: float = 0.0
    total_matches: int = 0
    error_message: Optional[str] = None
    matched_products: List[MatchedProductV2] = Field(default_factory=list)
    processing_stats: Optional[ProcessingStatsV2] = None


class ProcessingMetricsV2(BaseModel):
    """Метрики обработки"""
    classifier_time: Optional[float] = None
    matcher_time: float
    standardizer_time: Optional[float] = None
    total_time: float


class SummaryV2(BaseModel):
    """Итоговая статистика - расширенная версия"""
    processing_duration_seconds: float
    items_per_second: float
    parallel_batch_size: Optional[int] = None
    items_with_perfect_match: int
    items_with_good_match: int
    items_with_partial_match: int
    items_without_match: int
    items_with_errors: int
    average_match_score: float
    total_suppliers: int
    total_matched_products: int
    total_supplier_offers: int
    semantic_search_enabled: Optional[bool] = None
    algorithm_version: Optional[str] = None
    total_products_analyzed: Optional[int] = None
    total_semantic_filtered: Optional[int] = None


class TenderMatchingResultV2(BaseModel):
    """Результат обработки тендера в формате v2 - сохраняем структуру v1 с расширениями"""
    # Поля из v1
    tender_name: Optional[str] = None
    tender_number: Optional[str] = None
    processing_time: datetime = Field(default_factory=datetime.utcnow)  # Сохраняем имя из v1
    total_items: int = 0
    matched_items: int = 0
    item_matches: List[TenderItemMatchV2] = Field(default_factory=list)  # Сохраняем имя из v1
    summary: SummaryV2
    
    # Новые поля v2
    tender_max_price: Optional[float] = None
    processing_metrics: ProcessingMetricsV2
    created_at: Optional[datetime] = None  # Дополнительное поле для удобства