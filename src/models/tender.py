from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any, Optional
from enum import Enum


class TenderCharacteristic(BaseModel):
    """Характеристика товара в тендере"""
    id: Optional[int] = None
    name: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None
    type: Optional[str] = None  # Качественная/Количественная
    required: Optional[bool] = True
    changeable: Optional[bool] = False
    fillInstruction: Optional[str] = None


class TenderItem(BaseModel):
    """Товар в тендере"""
    id: Optional[int] = None
    name: Optional[str] = None
    okpd2Code: Optional[str] = Field(default=None, alias="okpd2Code")
    ktruCode: Optional[str] = Field(default=None, alias="ktruCode")
    quantity: Optional[float] = None
    unitOfMeasurement: Optional[str] = None
    unitPrice: Optional[Dict[str, Any]] = None
    totalPrice: Optional[Dict[str, Any]] = None
    characteristics: List[TenderCharacteristic] = Field(default_factory=list)
    additionalRequirements: Optional[str] = None
    okpd2Name: Optional[str] = None

    class Config:
        populate_by_name = True


class TenderDeliveryInfo(BaseModel):
    """Информация о доставке"""
    deliveryAddress: Optional[str] = None
    deliveryTerm: Optional[str] = None
    deliveryConditions: Optional[str] = None


class TenderPaymentInfo(BaseModel):
    """Информация об оплате"""
    paymentTerm: Optional[str] = None
    paymentMethod: Optional[str] = None
    paymentConditions: Optional[str] = None


class TenderInfo(BaseModel):
    """Информация о тендере"""
    tenderName: Optional[str] = None
    tenderNumber: Optional[str] = None
    customerName: Optional[str] = None
    description: Optional[str] = None
    purchaseType: Optional[str] = None
    financingSource: Optional[str] = None
    maxPrice: Optional[Dict[str, Any]] = None
    deliveryInfo: Optional[TenderDeliveryInfo] = None
    paymentInfo: Optional[TenderPaymentInfo] = None


class TenderRequest(BaseModel):
    """Запрос на обработку тендера"""
    tenderInfo: Optional[TenderInfo] = None
    items: List[TenderItem] = Field(default_factory=list)
    generalRequirements: Optional[Dict[str, Any]] = None
    attachments: Optional[List[Dict[str, Any]]] = Field(default_factory=list)


class MatchedSupplier(BaseModel):
    """Поставщик, подходящий под требования тендера"""
    supplier_key: str
    supplier_name: str
    supplier_tel: Optional[str] = None
    supplier_address: Optional[str] = None
    supplier_offers: List[Dict[str, Any]] = Field(default_factory=list)
    purchase_url: Optional[str] = None
    match_score: float = Field(..., description="Степень соответствия от 0 до 1")
    matched_attributes: List[str] = Field(default_factory=list, description="Список совпавших атрибутов")


class MatchedProduct(BaseModel):
    """Товар из БД, подходящий под требования тендера"""
    product_hash: str
    okpd2_code: str
    okpd2_name: str
    sample_title: Optional[str] = None
    sample_brand: Optional[str] = None
    standardized_attributes: List[Dict[str, Any]] = Field(default_factory=list)
    matched_suppliers: List[MatchedSupplier] = Field(default_factory=list)
    total_suppliers: int = 0
    match_score: float = Field(..., description="Общая степень соответствия")
    match_details: Dict[str, Any] = Field(default_factory=dict, description="Детали сопоставления")


class TenderItemMatch(BaseModel):
    """Результат сопоставления товара из тендера"""
    tender_item_id: Optional[int] = None
    tender_item_name: Optional[str] = None
    okpd2_code: Optional[str] = None
    matched_products: List[MatchedProduct] = Field(default_factory=list)
    total_matches: float = 0
    best_match_score: float = 0.0
    processing_status: str = "success"  # success, no_matches, error
    error_message: Optional[str] = None
    processing_stats: Optional[Dict[str, Any]] = None


class TenderMatchingResult(BaseModel):
    """Результат обработки тендера"""
    tender_number: Optional[str] = None
    tender_name: Optional[str] = None
    tender_max_price: Optional[float] = None
    processing_time: datetime = Field(default_factory=datetime.utcnow)
    total_items: int = 0
    matched_items: int = 0
    item_matches: List[TenderItemMatch] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict, description="Сводная информация")