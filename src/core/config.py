from pydantic_settings import BaseSettings
from typing import Optional
from urllib.parse import quote_plus


class Settings(BaseSettings):
    populate_by_name: bool = True

    # Unique Products MongoDB (БД с дедуплицированными товарами)
    unique_mongo_host: str = "localhost"
    unique_mongo_port: int = 27017
    unique_mongo_user: Optional[str] = None
    unique_mongo_pass: Optional[str] = None
    unique_mongo_authsource: Optional[str] = "admin"
    unique_mongo_authmechanism: str = "SCRAM-SHA-256"
    unique_mongo_direct_connection: bool = True  # Для прямого подключения
    unique_mongodb_database: str = "unique_products"
    unique_collection_name: str = "unique_products"
    unique_mongo_replica_set: Optional[str] = None  # Имя replica set
    unique_mongo_timeout: int = 10000  # Таймаут в миллисекундах

    # Processing settings
    min_match_score: float = 0.5  # Минимальный score для включения в результаты
    max_matched_products_per_item: int = 10  # Максимум подходящих товаров на позицию

    # API settings
    api_key: Optional[str] = None  # Если не указан - проверка отключена
    service_name: str = "tender_matching_service"
    service_port: int = 8002

    # Matching settings
    price_tolerance_percent: float = 20.0  # Допустимое отклонение цены в %
    enable_fuzzy_matching: bool = False  # Включить нечеткое сопоставление
    fuzzy_match_threshold: float = 0.8  # Порог для нечеткого сопоставления

    # OKPD2 search settings
    okpd2_search_depth: int = 3  # До какого уровня иерархии искать (1-3)
    okpd2_fallback_enabled: bool = True  # Расширять поиск при малом количестве результатов
    min_products_for_matching: int = 5  # Минимум товаров для расширения поиска
    okpd2_exact_match_weight: float = 2.0  # Вес для точного совпадения OKPD2

    # Performance settings
    enable_parallel_processing: bool = True  # Параллельная обработка товаров
    max_parallel_items: int = 10  # Максимум параллельных задач

    # Cache settings
    enable_okpd2_cache: bool = True
    okpd2_cache_ttl: int = 3600  # Время жизни кэша в секундах (1 час)
    okpd2_cache_size: int = 1000  # Размер LRU кэша

    # Logging settings
    log_level: str = "INFO"
    log_to_file: bool = True
    log_file_path: str = "logs/tender_matching.log"

    @property
    def unique_mongodb_connection_string(self) -> str:
        """Строка подключения для Unique Products MongoDB"""
        if self.unique_mongo_user and self.unique_mongo_pass:
            # Кодируем пароль для URL
            encoded_password = quote_plus(self.unique_mongo_pass)
            encoded_user = quote_plus(self.unique_mongo_user)

            # Для прямого подключения используем упрощенную строку
            if self.unique_mongo_direct_connection:
                # Простая строка с прямым подключением
                connection_string = (
                    f"mongodb://{encoded_user}:{encoded_password}@"
                    f"{self.unique_mongo_host}:{self.unique_mongo_port}/"
                    f"?authSource={self.unique_mongo_authsource or 'admin'}"
                    f"&authMechanism={self.unique_mongo_authmechanism}"
                    f"&directConnection=true"
                    f"&serverSelectionTimeoutMS={self.unique_mongo_timeout}"
                    f"&connectTimeoutMS={self.unique_mongo_timeout}"
                )
            else:
                # Строка для replica set
                connection_string = (
                    f"mongodb://{encoded_user}:{encoded_password}@"
                    f"{self.unique_mongo_host}:{self.unique_mongo_port}/"
                    f"?authSource={self.unique_mongo_authsource or 'admin'}"
                    f"&authMechanism={self.unique_mongo_authmechanism}"
                )

                if self.unique_mongo_replica_set:
                    connection_string += f"&replicaSet={self.unique_mongo_replica_set}"
                    connection_string += "&readPreference=primaryPreferred"

                connection_string += f"&serverSelectionTimeoutMS={self.unique_mongo_timeout}"
                connection_string += f"&connectTimeoutMS={self.unique_mongo_timeout}"
        else:
            # Без аутентификации
            connection_string = f"mongodb://{self.unique_mongo_host}:{self.unique_mongo_port}/"

            if self.unique_mongo_direct_connection:
                connection_string += "?directConnection=true"

            connection_string += f"&serverSelectionTimeoutMS={self.unique_mongo_timeout}"
            connection_string += f"&connectTimeoutMS={self.unique_mongo_timeout}"

        return connection_string

    class Config:
        env_file = ".env"


settings = Settings()