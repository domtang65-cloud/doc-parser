"""配置加载层：只做一件事 —— 把 config.yaml 读成 Python 对象，并允许环境变量覆盖"""
import os
from pathlib import Path

import yaml
from pydantic import BaseModel

# 项目根目录 = 本文件往上两级（core -> app -> 根）
BASE_DIR = Path(__file__).resolve().parents[2]


class AppConfig(BaseModel):
    name: str = "doc-parser"
    version: str = "0.1.0"
    debug: bool = True
    log_dir: str = "logs"
    log_level: str = "INFO"
    max_file_size_mb: int = 50
    allowed_extensions: list[str] = [".pdf", ".docx", ".txt", ".xlsx"]
    max_pdf_pages: int = 200


def _load_yaml(path: Path) -> dict:
    """配置文件不存在也不崩，用默认值跑 —— 这是健壮性，不是偷懒"""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


settings = AppConfig(**_load_yaml(BASE_DIR / "config" / "config.yaml"))

# 环境变量覆盖：客户现场不方便改文件时，用 APP_LOG_LEVEL=DEBUG 临时调高日志
if os.getenv("APP_LOG_LEVEL"):
    settings.log_level = os.getenv("APP_LOG_LEVEL")
if os.getenv("APP_DEBUG"):
    settings.debug = os.getenv("APP_DEBUG") == "1"
