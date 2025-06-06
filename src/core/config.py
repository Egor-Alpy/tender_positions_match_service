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
    unique_mongo_authsource: Optional[str] = None
    unique_mongo_authmechanism: str = "SCRAM-SHA-256"
    unique_mongo_direct_connection: bool = False
    unique_mongodb_database: str = "unique_products"
    unique_collection_name: str = "unique_products"
    unique_mongo_replica_set: Optional[str] = None  # Имя replica set

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

    @property
    def unique_mongodb_connection_string(self) -> str:
        """Строка подключения для Unique Products MongoDB"""
        if self.unique_mongo_user and self.unique_mongo_pass:
            # Кодируем пароль для URL
            from urllib.parse import quote_plus
            encoded_password = quote_plus(self.unique_mongo_pass)

            # Для прямого подключения используем упрощенную строку
            if self.unique_mongo_direct_connection:
                # Простая строка без указания БД в пути
                connection_string = (
                    f"mongodb://{self.unique_mongo_user}:{encoded_password}@"
                    f"{self.unique_mongo_host}:{self.unique_mongo_port}/"
                    f"?authSource={self.unique_mongo_authsource or 'admin'}"
                    f"&directConnection=true"
                )
            else:
                # Стандартная строка подключения
                connection_string = (
                    f"mongodb://{self.unique_mongo_user}:{encoded_password}@"
                    f"{self.unique_mongo_host}:{self.unique_mongo_port}/"
                    f"{self.unique_mongodb_database}"
                )

                # Добавляем параметры
                params = []
                if self.unique_mongo_authsource:
                    params.append(f"authSource={self.unique_mongo_authsource}")
                params.append(f"authMechanism={self.unique_mongo_authmechanism}")

                if self.unique_mongo_replica_set:
                    params.append(f"replicaSet={self.unique_mongo_replica_set}")
                    params.append("readPreference=primaryPreferred")

                if params:
                    connection_string += "?" + "&".join(params)
        else:
            connection_string = f"mongodb://{self.unique_mongo_host}:{self.unique_mongo_port}/{self.unique_mongodb_database}"

        return connection_string

    class Config:
        env_file = ".env"


settings = Settings()