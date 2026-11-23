"""解析器单测：不依赖外部文件，测试自己造，保证别人 clone 下来就能跑"""
import pymupdf
import pytest
from docx import Document

from app.services.cleaner import clean_text
from app.services.parsers import parse_by_suffix, parse_txt
from app.services.parsers import parse_pdf, parse_docx


def test_txt_gbk_not_garbled(tmp_path):
    p = tmp_path / "gbk.txt"
    p.write_bytes("中文测试内容".encode("gb18030"))
    r = parse_txt(p)
    assert r["encoding"] == "gb18030"
    assert "中文测试" in r["text"]


def test_pdf_basic(tmp_path):
    p = tmp_path / "a.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Hello PDF", fontsize=14)
    doc.save(str(p))
    doc.close()

    r = parse_pdf(p)
    assert r["page_count"] == 1
    assert "Hello PDF" in r["text"]


def test_docx_paragraph_and_table(tmp_path):
    p = tmp_path / "a.docx"
    d = Document()
    d.add_paragraph("第一段正文")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "姓名"
    t.cell(1, 0).text = "张三"
    d.save(str(p))

    r = parse_docx(p)
    assert any(b["type"] == "table" for b in r["blocks"])
    assert "第一段正文" in r["text"]


def test_clean_text_removes_control_and_full_width_space():
    dirty = "合同\x00\t\t  \u3000甲方：张三\n\n\n\n乙方"
    out = clean_text(dirty)
    assert "\x00" not in out
    assert "\u3000" not in out
    assert "甲方" in out


def test_unsupported_suffix(tmp_path):
    p = tmp_path / "x.exe"
    p.write_bytes(b"binary")
    with pytest.raises(Exception):
        parse_by_suffix(p, ".exe")


def test_clean_ratio_in_result(tmp_path):
    p = tmp_path / "d.txt"
    p.write_bytes("   \n\n\n  内容   \n\n\n".encode("utf-8"))
    r = parse_by_suffix(p, ".txt")
    assert r["char_count"] > 0
    assert 0 < r["cleaned_ratio"] <= 1.0