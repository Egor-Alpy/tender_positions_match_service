from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any, Optional
from enum import Enum


class TenderCharacteristic(BaseModel):
    """Характеристика товара в тендере"""
    id: int
    name: str
    value: str
    unit: Optional[str] = None
    type: str  # Качественная/Количественная
    required: bool = True
    changeable: bool = False
    fillInstruction: Optional[str] = None


class TenderItem(BaseModel):
    """Товар в тендере"""
    id: int
    name: str
    okpd2Code: str = Field(default="", alias="okpd2Code")  # Может быть пустым
    ktruCode: str = Field(default="", alias="ktruCode")  # Может быть пустым
    quantity: int = 0  # По умолчанию 0
    unitOfMeasurement: str = ""  # По умолчанию пустая строка
    unitPrice: Dict[str, Any]
    totalPrice: Dict[str, Any]
    characteristics: List[TenderCharacteristic] = Field(default_factory=list)  # Пустой список по умолчанию
    additionalRequirements: Optional[str] = None
    okpd2Name: Optional[str] = None  # Добавлено поле для имени OKPD2

    class Config:
        populate_by_name = True


class TenderDeliveryInfo(BaseModel):
    """Информация о доставке"""
    deliveryAddress: str
    deliveryTerm: str
    deliveryConditions: str


class TenderPaymentInfo(BaseModel):
    """Информация об оплате"""
    paymentTerm: str
    paymentMethod: str
    paymentConditions: str


class TenderInfo(BaseModel):
    """Информация о тендере"""
    tenderName: str
    tenderNumber: str
    customerName: str
    description: Optional[str] = None
    purchaseType: str
    financingSource: str
    maxPrice: Dict[str, Any]
    deliveryInfo: TenderDeliveryInfo
    paymentInfo: TenderPaymentInfo


class TenderRequest(BaseModel):
    """Запрос на обработку тендера"""
    tenderInfo: TenderInfo
    items: List[TenderItem]
    generalRequirements: Optional[Dict[str, Any]] = None
    attachments: Optional[List[Dict[str, Any]]] = []


class MatchedSupplier(BaseModel):
    """Поставщик, подходящий под требования тендера"""
    supplier_key: str
    supplier_name: str
    supplier_tel: Optional[str] = None
    supplier_address: Optional[str] = None
    supplier_offers: List[Dict[str, Any]]
    purchase_url: Optional[str] = None
    match_score: float = Field(..., description="Степень соответствия от 0 до 1")
    matched_attributes: List[str] = Field(..., description="Список совпавших атрибутов")


class MatchedProduct(BaseModel):
    """Товар из БД, подходящий под требования тендера"""
    product_hash: str
    okpd2_code: str
    okpd2_name: str
    sample_title: Optional[str] = None
    sample_brand: Optional[str] = None
    standardized_attributes: List[Dict[str, Any]]
    matched_suppliers: List[MatchedSupplier]
    total_suppliers: int
    match_score: float = Field(..., description="Общая степень соответствия")
    match_details: Dict[str, Any] = Field(..., description="Детали сопоставления")


class TenderItemMatch(BaseModel):
    """Результат сопоставления товара из тендера"""
    tender_item_id: int
    tender_item_name: str
    okpd2_code: str
    matched_products: List[MatchedProduct]
    total_matches: int
    best_match_score: float
    processing_status: str = "success"  # success, no_matches, error
    error_message: Optional[str] = None


class TenderMatchingResult(BaseModel):
    """Результат обработки тендера"""
    tender_number: str
    tender_name: str
    processing_time: datetime = Field(default_factory=datetime.utcnow)
    total_items: int
    matched_items: int
    item_matches: List[TenderItemMatch]
    summary: Dict[str, Any] = Field(..., description="Сводная информация")