"""解析层：按格式分发，返回统一结构（索引版 text + 原文版 text_verbatim）"""
import logging
from pathlib import Path

import pymupdf                     # PyMuPDF 2.x
import pandas as pd
from docx import Document

from app.core.config import settings
from app.core.errors import ParseFailed
from app.services.cleaner import build_text_views, clean_for_display

logger = logging.getLogger(__name__)


def _with_views(result: dict, raw_text: str, name: str = "") -> dict:
    """给结果挂上两份文本视图，并在这里做**全格式统一的质量校验**。

    text          = 索引版（NFKC 归一，供检索 / RAG 入库）
    text_verbatim = 原文版（NFC，只删控制字符，供溯源展示）
    不变式：clean_for_index(text_verbatim) == text

    两道通用校验（一处覆盖所有格式，不在各解析器里各写一遍）：
    ① 空文本 → 抛 ParseFailed：纯控制字符文件（如 \x00\x01\x02）、全空白页、
       空 sheet 都会清成空串，静默返回 200 + 空 text 是客户现场最难查的形态；
    ② 清洗率过低 → 打 WARNING：ratio < 0.5 且原文 >= 20 字符时告警，
       常见根因是编码识别错（GBK 被当 utf-8）或图片型文档，也可能是清洗规则过猛。
    """
    views = build_text_views(raw_text)
    if not views["text"].strip():
        logger.warning("解析后无有效文本：%s", name or "?")
        raise ParseFailed(
            "文件解析后无有效文本（可能全是空白/控制字符，或扫描件）",
            {
                "raw_char_count": views["raw_char_count"],
                "removed_control_chars": views["removed_control_chars"],
            },
        )

    if views["raw_char_count"] >= 20 and views["cleaned_ratio"] < 0.5:
        logger.warning(
            "文本被清洗掉 %.0f%%（疑似编码错误 / 图片型文档 / 清洗过猛）：%s raw=%d index=%d",
            (1 - views["cleaned_ratio"]) * 100, name or "?",
            views["raw_char_count"], views["char_count"],
        )

    result.update(views)
    return result


def parse_pdf(path: Path, name: str = "") -> dict:
    """PDF → 逐页文本。带页数上限保护，防止 500 页文档把服务拖死

    name: 用户上传的原始文件名，只用于日志。临时文件叫 upload.pdf，
    日志里打它等于没打——出问题时认不出是哪个客户、哪个文件。
    """
    log_name = name or path.name
    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:
        # 对外一句人话；pymupdf / mupdf 的内部报错只进日志
        logger.warning("PDF 打开失败（技术细节）：%s | %s", log_name, exc, exc_info=True)
        raise ParseFailed(
            "PDF 文件无法打开（可能已损坏或受密码保护），请确认文件完整后重试",
            {
                "reason": type(exc).__name__,
                "hint": "常见原因：下载未完成、文件损坏、PDF 已加密",
            },
        ) from exc

    try:
        if doc.is_encrypted:
            raise ParseFailed("PDF 已加密，请提供未加密版本")

        max_pages = settings.max_pdf_pages
        raw_pages = []      # 原样文本：算覆盖率、算控制字符数
        pages = []          # 展示版文本：给溯源用
        for i in range(min(doc.page_count, max_pages)):
            t = doc[i].get_text("text")
            raw_pages.append(t)
            pages.append({"page": i + 1, "text": clean_for_display(t)})

        raw_text = "\n".join(raw_pages)
        empty_ratio = sum(1 for t in raw_pages if not t.strip()) / max(len(raw_pages), 1)

        # 智能判断：大比例空页 = 扫描件，这是最常见的"客户说'你们系统读不出来'"的原因
        if empty_ratio > 0.8:
            logger.warning("疑似扫描件（%.0f%% 页面无文本层）：%s", empty_ratio * 100, log_name)
            raise ParseFailed(
                "该 PDF 疑似扫描件，无文本层，需 OCR 处理",
                {"empty_page_ratio": round(empty_ratio, 2), "page_count": doc.page_count},
            )

        # 0 页 / 全空页文档：覆盖率算出来是 0，上面那条拦不住，会静默返回空文本
        if not raw_text.strip():
            raise ParseFailed(
                "PDF 无可提取文本（空页或文件损坏）",
                {"page_count": doc.page_count},
            )

        return _with_views({
            "type": "pdf",
            "page_count": doc.page_count,
            "truncated": doc.page_count > max_pages,
            "pages": pages,
        }, raw_text, log_name)
    finally:
        doc.close()          # 必须 close，否则文件句柄泄漏 —— Windows 上会删不掉临时文件


def parse_docx(path: Path, name: str = "") -> dict:
    """Word → 段落 + 表格。注意：此处按'先段落再表格'顺序读取，不是文档真实顺序"""
    log_name = name or path.name
    try:
        doc = Document(str(path))
    except Exception as exc:
        # python-docx 抛的是 PackageNotFoundError（英文），不能直接给客户
        logger.warning("Word 打开失败（技术细节）：%s | %s", log_name, exc, exc_info=True)
        raise ParseFailed(
            "Word 文件无法打开（可能已损坏，或为旧版 .doc），请用 Word 另存为 .docx 后重试",
            {
                "reason": type(exc).__name__,
                "hint": "常见原因：文件损坏、旧版 .doc 改了后缀、文档有密码",
            },
        ) from exc

    blocks = []
    raw_paras = []          # 段落原样文本：只有它进 text / text_verbatim

    for para in doc.paragraphs:
        raw = para.text
        if not raw.strip():
            continue
        raw_paras.append(raw)
        t = clean_for_display(raw).strip()
        # 用样式名大致区分标题，为后续按标题分片做准备
        style = (para.style.name or "").lower()
        if "heading" in style or "标题" in style:
            blocks.append({"type": "heading", "level": style, "text": t})
        else:
            blocks.append({"type": "paragraph", "text": t})

    for idx, table in enumerate(doc.tables, start=1):
        rows = []
        for row in table.rows:
            rows.append([clean_for_display(cell.text).strip() for cell in row.cells])
        blocks.append({"type": "table", "table_index": idx, "rows": rows})

    raw_text = "\n".join(raw_paras)

    return _with_views({
        "type": "docx",
        "block_count": len(blocks),
        "blocks": blocks,
        "known_limitation": "段落与表格按类型分组读取，非文档原始顺序；表格内容只在 blocks 里，未进 text",
    }, raw_text, log_name)


def parse_txt(path: Path, name: str = "") -> dict:
    """TXT → 文本。自动尝试多种编码，客户给 GBK 也不会乱码"""
    log_name = name or path.name
    raw = path.read_bytes()
    used_encoding = None
    # BOM 只按字节判断，不靠"试解码"：utf-8-sig 在无 BOM 文件上也能成功解码，
    # 若让它排在最前面，普通 UTF-8 文件会被误标成 utf-8-sig（标签失真）。
    # 带 BOM（EF BB BF）→ 用 utf-8-sig 解，顺手把 BOM 那个隐形字符吃掉；
    # 不带 → 直接 utf-8，标签才是真的。
    if raw.startswith(b"\xef\xbb\xbf"):
        candidates = ("utf-8-sig", "utf-8", "gb18030", "utf-16")
    else:
        candidates = ("utf-8", "gb18030", "utf-16")
    for enc in candidates:
        try:
            text = raw.decode(enc)
            used_encoding = enc
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
        used_encoding = "utf-8(replace)"

    return _with_views({
        "type": "txt",
        "encoding": used_encoding,
    }, text, log_name)


def parse_excel(path: Path, name: str = "") -> dict:
    """Excel → 每个 sheet 的行列。依赖 openpyxl"""
    log_name = name or path.name
    try:
        sheets = pd.read_excel(path, sheet_name=None)   # None = 读所有 sheet
    except Exception as exc:
        # 对外：一句人话 + 可渲染的 details；技术细节只进日志（否则等于告诉客户"我用 pandas"）
        logger.warning("Excel 解析失败（技术细节）：%s | %s", log_name, exc, exc_info=True)
        raise ParseFailed(
            "文件内容与 .xlsx 格式不符，请确认后用 Excel 重新导出",
            {
                "reason": type(exc).__name__,
                "hint": "常见原因：把 PDF/CSV/旧版 .xls 直接改了后缀，或文件已损坏",
            },
        ) from exc

    result = {}
    text_parts = []
    for name, df in sheets.items():
        result[name] = {
            "shape": list(df.shape),
            "columns": [str(c) for c in df.columns],
            "rows": df.fillna("").astype(str).values.tolist(),
        }
        text_parts.append(f"[Sheet: {name}]\n" + df.fillna("").to_csv(index=False))

    return _with_views({
        "type": "xlsx",
        "sheet_count": len(sheets),
        "sheets": result,
    }, "\n".join(text_parts), log_name)


def parse_by_suffix(path: Path, suffix: str, name: str = "") -> dict:
    """按后缀分发。

    path 是临时文件路径（upload.pdf 之类），name 是用户的原始文件名：
    日志一律用 name，排障时才对得上人。
    """
    suffix = suffix.lower()
    display = name or path.name
    parsers = {
        ".pdf": parse_pdf,
        ".docx": parse_docx,
        ".txt": parse_txt,
        ".xlsx": parse_excel,
    }
    func = parsers.get(suffix)
    if not func:
        raise ParseFailed(f"暂未支持的格式：{suffix}")
    return func(path, display)
