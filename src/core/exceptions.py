class TenderMatchingException(Exception):
    """Базовое исключение для сервиса сопоставления тендеров"""
    pass


class DatabaseConnectionException(TenderMatchingException):
    """Исключение при подключении к БД"""
    pass


class TenderProcessingException(TenderMatchingException):
    """Исключение при обработке тендера"""
    pass


class InvalidTenderDataException(TenderMatchingException):
    """Исключение при невалидных данных тендера"""
    pass


class ProductMatchingException(TenderMatchingException):
    """Исключение при сопоставлении товаров"""
    pass


class ConfigurationException(TenderMatchingException):
    """Исключение конфигурации"""
    pass