from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import logging

from src.core.config import settings
from src.models.tender import TenderMatchingResult

logger = logging.getLogger(__name__)


class TenderResultsMongoStore:
    """Хранилище результатов обработки тендеров"""

    def __init__(self, database_name: str, collection_name: str = "matching_results"):
        self.client = AsyncIOMotorClient(
            settings.results_mongodb_connection_string,
            directConnection=settings.results_mongo_direct_connection,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000
        )
        self.db: AsyncIOMotorDatabase = self.client[database_name]
        self.collection = self.db[collection_name]

    async def initialize(self):
        """Инициализация хранилища и создание индексов"""
        connected = await self.test_connection()
        if not connected:
            raise Exception("Failed to connect to results MongoDB")

        await self._setup_indexes()

    async def _setup_indexes(self):
        """Создать необходимые индексы"""
        try:
            # Уникальный индекс по номеру тендера и времени обработки
            await self.collection.create_index([
                ("tender_number", 1),
                ("processing_time", -1)
            ])

            # Индексы для поиска
            await self.collection.create_index("tender_number")
            await self.collection.create_index("processing_time")
            await self.collection.create_index("task_id")

            # TTL индекс для автоматического удаления старых результатов
            await self.collection.create_index(
                "processing_time",
                expireAfterSeconds=30 * 24 * 60 * 60  # 30 дней
            )

            logger.info("Tender results indexes created successfully")
        except Exception as e:
            logger.warning(f"Error creating indexes (may already exist): {e}")

    async def save_result(
            self,
            task_id: str,
            result: TenderMatchingResult
    ) -> str:
        """Сохранить результат обработки тендера"""
        try:
            document = result.dict()
            document["task_id"] = task_id
            document["created_at"] = datetime.utcnow()

            result = await self.collection.insert_one(document)
            logger.info(f"Saved tender result: {result.inserted_id}")
            return str(result.inserted_id)

        except Exception as e:
            logger.error(f"Error saving tender result: {e}")
            raise

    async def get_result_by_task_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Получить результат по ID задачи"""
        result = await self.collection.find_one({"task_id": task_id})
        if result:
            result["_id"] = str(result["_id"])
        return result

    async def get_results_by_tender_number(
            self,
            tender_number: str,
            limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Получить историю обработки тендера"""
        cursor = self.collection.find(
            {"tender_number": tender_number}
        ).sort("processing_time", -1).limit(limit)

        results = await cursor.to_list(length=limit)

        # Преобразуем ObjectId в строки
        for result in results:
            result["_id"] = str(result["_id"])

        return results

    async def get_recent_results(
            self,
            days: int = 7,
            limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Получить недавние результаты"""
        since_date = datetime.utcnow() - timedelta(days=days)

        cursor = self.collection.find(
            {"processing_time": {"$gte": since_date}}
        ).sort("processing_time", -1).limit(limit)

        results = await cursor.to_list(length=limit)

        for result in results:
            result["_id"] = str(result["_id"])

        return results

    async def get_statistics(self, days: int = 7) -> Dict[str, Any]:
        """Получить статистику по обработанным тендерам"""
        since_date = datetime.utcnow() - timedelta(days=days)

        pipeline = [
            {"$match": {"processing_time": {"$gte": since_date}}},
            {"$facet": {
                "total": [{"$count": "count"}],
                "by_items": [
                    {"$group": {
                        "_id": None,
                        "total_items": {"$sum": "$total_items"},
                        "matched_items": {"$sum": "$matched_items"},
                        "avg_items_per_tender": {"$avg": "$total_items"},
                        "avg_matched_per_tender": {"$avg": "$matched_items"}
                    }}
                ],
                "by_quality": [
                    {"$unwind": "$item_matches"},
                    {"$group": {
                        "_id": {
                            "$switch": {
                                "branches": [
                                    {"case": {"$gte": ["$item_matches.best_match_score", 0.9]}, "then": "perfect"},
                                    {"case": {"$gte": ["$item_matches.best_match_score", 0.7]}, "then": "good"},
                                    {"case": {"$gte": ["$item_matches.best_match_score", 0.5]}, "then": "partial"},
                                    {"case": {"$eq": ["$item_matches.best_match_score", 0]}, "then": "no_match"}
                                ],
                                "default": "other"
                            }
                        },
                        "count": {"$sum": 1}
                    }}
                ],
                "processing_times": [
                    {"$group": {
                        "_id": None,
                        "avg_time": {"$avg": "$summary.processing_duration_seconds"},
                        "min_time": {"$min": "$summary.processing_duration_seconds"},
                        "max_time": {"$max": "$summary.processing_duration_seconds"}
                    }}
                ],
                "by_day": [
                    {"$group": {
                        "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$processing_time"}},
                        "count": {"$sum": 1},
                        "total_items": {"$sum": "$total_items"},
                        "matched_items": {"$sum": "$matched_items"}
                    }},
                    {"$sort": {"_id": -1}}
                ]
            }}
        ]

        cursor = self.collection.aggregate(pipeline)
        result = await cursor.to_list(length=1)

        if not result:
            return {
                "total_tenders": 0,
                "period_days": days
            }

        facets = result[0]

        stats = {
            "period_days": days,
            "total_tenders": facets["total"][0]["count"] if facets["total"] else 0,
            "items_stats": facets["by_items"][0] if facets["by_items"] else {},
            "match_quality": {
                item["_id"]: item["count"]
                for item in facets["by_quality"]
            },
            "processing_times": facets["processing_times"][0] if facets["processing_times"] else {},
            "by_day": facets["by_day"]
        }

        # Рассчитываем общий процент совпадений
        if stats["items_stats"]:
            total = stats["items_stats"]["total_items"]
            matched = stats["items_stats"]["matched_items"]
            stats["overall_match_rate"] = (matched / total * 100) if total > 0 else 0

        return stats

    async def cleanup_old_results(self, days_to_keep: int = 30) -> int:
        """Удалить старые результаты"""
        cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)

        result = await self.collection.delete_many(
            {"processing_time": {"$lt": cutoff_date}}
        )

        logger.info(f"Deleted {result.deleted_count} old tender results")
        return result.deleted_count

    async def test_connection(self) -> bool:
        """Проверить подключение к БД"""
        try:
            await self.client.admin.command('ping')
            logger.info("Successfully connected to results MongoDB")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to results MongoDB: {e}")
            return False

    async def close(self):
        """Закрыть соединение"""
        self.client.close()