from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

from src.core.config import settings

logger = logging.getLogger(__name__)


class UniqueProductsMongoStore:
    """Работа с MongoDB уникальных товаров (только чтение)"""

    def __init__(self, database_name: str, collection_name: str = "unique_products"):
        # Добавляем directConnection для работы с одним узлом ReplicaSet
        connection_options = {
            "serverSelectionTimeoutMS": 5000,
            "connectTimeoutMS": 5000,
        }

        # Если указано прямое подключение
        if settings.unique_mongo_direct_connection:
            connection_options["directConnection"] = True

        self.client = AsyncIOMotorClient(
            settings.unique_mongodb_connection_string,
            **connection_options
        )
        self.db: AsyncIOMotorDatabase = self.client[database_name]
        self.collection = self.db[collection_name]
        self._connected = False

    async def initialize(self):
        """Инициализация хранилища"""
        self._connected = await self.test_connection()
        if not self._connected:
            logger.warning("Working without MongoDB connection - will return empty results")

    async def find_by_hash(self, product_hash: str) -> Optional[Dict[str, Any]]:
        """Найти товар по хешу"""
        if not self._connected:
            return None

        try:
            product = await self.collection.find_one({"product_hash": product_hash})
            if product:
                product["_id"] = str(product["_id"])
            return product
        except Exception as e:
            logger.error(f"Error finding product by hash: {e}")
            return None

    async def find_products(
            self,
            filters: Dict[str, Any] = None,
            limit: int = 100,
            skip: int = 0,
            sort_by: str = "unique_suppliers_count",
            sort_order: int = -1
    ) -> List[Dict[str, Any]]:
        """Поиск уникальных товаров"""
        if not self._connected:
            return []

        try:
            query = filters or {}

            cursor = self.collection.find(query)
            cursor = cursor.sort(sort_by, sort_order)
            cursor = cursor.skip(skip).limit(limit)

            products = await cursor.to_list(length=limit)

            # Преобразуем ObjectId в строки
            for product in products:
                product["_id"] = str(product["_id"])

            return products
        except Exception as e:
            logger.error(f"Error finding products: {e}")
            return []

    async def find_by_original_product(self, original_mongo_id: str) -> Optional[Dict[str, Any]]:
        """Найти уникальный товар по ID исходного товара"""
        if not self._connected:
            return None

        try:
            product = await self.collection.find_one({
                "source_products.original_mongo_id": original_mongo_id
            })

            if product:
                product["_id"] = str(product["_id"])

            return product
        except Exception as e:
            logger.error(f"Error finding product by original ID: {e}")
            return None

    async def search_products(
            self,
            search_text: str,
            limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Текстовый поиск товаров"""
        if not self._connected:
            return []

        try:
            cursor = self.collection.find(
                {"$text": {"$search": search_text}},
                {"score": {"$meta": "textScore"}}
            )
            cursor = cursor.sort([("score", {"$meta": "textScore"})])
            cursor = cursor.limit(limit)

            products = await cursor.to_list(length=limit)

            for product in products:
                product["_id"] = str(product["_id"])

            return products
        except Exception as e:
            logger.error(f"Error searching products: {e}")
            return []

    async def get_statistics(self) -> Dict[str, Any]:
        """Получить статистику по уникальным товарам"""
        if not self._connected:
            return {
                "total_unique_products": 0,
                "by_okpd_class": {},
                "deduplication_rate": 0,
                "error": "No database connection"
            }

        try:
            # Упрощенная статистика - просто количество документов
            total_count = await self.collection.count_documents({})

            # Попробуем получить базовую статистику по OKPD
            okpd_stats = {}
            try:
                # Получаем несколько документов для примера
                sample_docs = await self.collection.find({}).limit(100).to_list(length=100)

                # Группируем по первым 2 цифрам OKPD
                for doc in sample_docs:
                    okpd = doc.get("okpd2_code", "")
                    if okpd:
                        okpd_class = okpd[:2]
                        if okpd_class not in okpd_stats:
                            okpd_stats[okpd_class] = {"products": 0, "suppliers": 0}
                        okpd_stats[okpd_class]["products"] += 1
                        okpd_stats[okpd_class]["suppliers"] += doc.get("unique_suppliers_count", 0)
            except Exception as e:
                logger.warning(f"Could not get OKPD statistics: {e}")

            return {
                "total_unique_products": total_count,
                "by_okpd_class": okpd_stats,
                "deduplication_rate": 0  # Не можем рассчитать без полной статистики
            }

        except Exception as e:
            logger.error(f"Error getting statistics: {e}")
            return {
                "total_unique_products": 0,
                "by_okpd_class": {},
                "deduplication_rate": 0,
                "error": str(e)
            }

    async def test_connection(self) -> bool:
        """Проверить подключение к БД"""
        try:
            # Простая проверка - пробуем прочитать один документ
            await self.client.admin.command('ping')

            # Пробуем прочитать из коллекции
            try:
                # Используем find с limit вместо find_one для лучшей совместимости
                cursor = self.collection.find({}).limit(1)
                docs = await cursor.to_list(length=1)

                if docs is not None:  # Может быть пустой список, но не None
                    logger.info(f"Successfully connected to MongoDB collection {self.collection.name}")
                    # Проверяем количество документов
                    try:
                        count = await self.collection.count_documents({})
                        logger.info(f"Found {count} documents in collection")
                    except:
                        logger.info("Connected to collection (count not available)")
                    return True
                else:
                    logger.warning("Could not read from collection")
                    return False

            except Exception as e:
                logger.warning(f"Could not access collection directly: {e}")
                # Но ping прошел, так что соединение есть
                return True

        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            return False

    async def close(self):
        """Закрыть соединение"""
        self.client.close()