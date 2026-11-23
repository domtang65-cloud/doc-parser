"""接口层：只做 HTTP 相关的事（收文件、校验、调服务、返回），不写业务逻辑"""
import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Query, UploadFile

from app.core.config import settings
from app.core.errors import FileTooLarge, FileTypeNotAllowed
from app.services.parsers import parse_by_suffix

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["documents"])

MAX_BYTES = settings.max_file_size_mb * 1024 * 1024


@router.post("/documents/parse", summary="上传文档并解析为结构化文本")
async def parse_document(
    file: UploadFile = File(...),
    verbatim: bool = Query(
        False,
        description="是否返回原文版 text_verbatim。默认 False：只回清洗后的 text，避免响应体翻倍",
    ),
):
    filename = file.filename or "unnamed"
    suffix = Path(filename).suffix.lower()

    # 1) 类型白名单：必须在读文件前校验，省 IO
    if suffix not in settings.allowed_extensions:
        raise FileTypeNotAllowed(
            f"不支持的格式：{suffix or '未知'}",
            {"allowed": settings.allowed_extensions},
        )

    # 2) 边读边计大小：避免把 2G 文件一次读进内存
    tmp_dir = Path(tempfile.mkdtemp(prefix="docparser_"))
    tmp_path = tmp_dir / f"upload{suffix}"
    size = 0
    try:
        with tmp_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)   # 每次 1MB
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise FileTooLarge(
                        f"文件超过 {settings.max_file_size_mb}MB 限制",
                        {"limit_mb": settings.max_file_size_mb},
                    )
                out.write(chunk)

        logger.info("收到文件 %s，%.2fMB", filename, size / 1024 / 1024)

        # 3) 解析（业务逻辑在 services 层）
        result = parse_by_suffix(tmp_path, suffix, filename)
        # 接口层口径：默认只回清洗版 text；原文版按需取，避免 200 页 PDF 响应体翻倍
        if not verbatim:
            result.pop("text_verbatim", None)
        return {
            "filename": filename,
            "suffix": suffix,
            "size_bytes": size,
            **result,
        }
    finally:
        # 4) 无论成败都清临时文件，否则客户服务器磁盘一个月就满
        shutil.rmtree(tmp_dir, ignore_errors=True)