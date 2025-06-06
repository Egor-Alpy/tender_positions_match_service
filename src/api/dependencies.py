from fastapi import Depends, HTTPException, Header
from typing import Optional

from src.core.config import settings


async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """Проверка API ключа"""
    if not settings.api_key:
        # Если API ключ не настроен - пропускаем проверку (для разработки)
        return None

    if not x_api_key or x_api_key != settings.api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key"
        )
    return x_api_key