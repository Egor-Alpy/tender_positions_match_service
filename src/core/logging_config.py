import logging
import sys
from logging.handlers import RotatingFileHandler
import os
from datetime import datetime


def setup_logging(
        name: str = None,
        level: str = "INFO",
        log_file: str = None,
        log_to_console: bool = True,
        log_format: str = None
):
    """
    Настройка логирования для приложения

    Args:
        name: Имя логгера
        level: Уровень логирования
        log_file: Путь к файлу логов (опционально)
        log_to_console: Выводить ли логи в консоль
        log_format: Формат логов
    """
    if log_format is None:
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Получаем логгер
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    # Удаляем существующие handlers чтобы избежать дублирования
    logger.handlers = []

    # Форматтер
    formatter = logging.Formatter(log_format)

    # Console handler
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, level.upper()))
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # File handler
    if log_file:
        # Создаем директорию если не существует
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5
        )
        file_handler.setLevel(getattr(logging, level.upper()))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def setup_app_logging(service_name: str = "tender_matching", level: str = "INFO"):
    """
    Настройка логирования для всего приложения

    Args:
        service_name: Имя сервиса
        level: Уровень логирования
    """
    # Настраиваем корневой логгер
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

    # Настраиваем логирование для всех модулей src
    src_logger = logging.getLogger('src')
    src_logger.setLevel(getattr(logging, level.upper()))

    # Создаем файл логов с датой
    log_filename = f"logs/{service_name}_{datetime.now().strftime('%Y%m%d')}.log"

    # Добавляем file handler
    setup_logging(
        name='src',
        level=level,
        log_file=log_filename,
        log_to_console=True
    )

    # Настраиваем уровни для сторонних библиотек
    logging.getLogger('uvicorn').setLevel(logging.INFO)
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('motor').setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured for {service_name}")
    logger.info(f"Log level: {level}")
    logger.info(f"Log file: {log_filename}")

    return logger