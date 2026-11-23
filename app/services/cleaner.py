"""清洗层：两套口径 —— 索引版（供检索召回）与原文版（供溯源展示）

为什么必须两套：
  索引版做 NFKC 归一（①→1、ﬁ→fi、全角标点→半角），召回更准；
  但同一步会让"原文"变样，客户拿原文来对照时会对不上。
所以：text（索引版）给检索用，text_verbatim（原文版）给展示用。
两者同源，可用 clean_for_index(text_verbatim) == text 互相验证。
"""
import re
import unicodedata

# C0/C1 控制字符：视觉不可见，但会让 JSON / 数据库写入炸（PDF 抽取常带 \x00）
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NEWLINE = re.compile(r"\r\n|\r")


def clean_for_display(text: str) -> str:
    """原文版清洗：只做"不改字"的三件事，其余原样保留。

    1) NFC：合并等价字形（é 的两种写法合成一种），不改动任何可见字符；
    2) 删除控制字符（不可见、会炸序列化）—— 这是唯一"删内容"的地方；
    3) 统一换行符 \\r\\n / \\r → \\n（格式归一，不是内容改动）。

    禁止在这里做：空白压缩、空行删除、strip。
    这些会让"第 3 段"和 PDF 原文对不上，溯源就名存实亡了。
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = _CONTROL.sub("", text)
    return _NEWLINE.sub("\n", text)


def clean_for_index(text: str) -> str:
    """索引版清洗：在原文版基础上做"检索友好"的归一，允许改字。

    额外四步：
    4) NFKC：全角标点/字母/数字折半角，①→1，ﬁ→fi —— 召回更准；
    5) 全角空格 → 半角空格；
    6) 行内连续空白压成一个空格（保留换行，段落结构靠换行）；
    7) 三个以上连续换行压成两个；逐行去首尾空白、丢掉空行。

    注意 NFKC 必须在 NFC 之后做（clean_for_display 已先做 NFC），否则不幂等。
    """
    text = clean_for_display(text)
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [ln.strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def count_control_chars(text: str) -> int:
    """数原文里有多少控制字符 —— 审计用：客户问"为什么少了字符"要答得出来"""
    return len(_CONTROL.findall(text or ""))


def build_text_views(raw: str) -> dict:
    """一次产出两份视图 + 可审计的计数，供解析层统一挂载。

    不变式：clean_for_index(text_verbatim) == text
    即"两份衣服裹的是同一份内容"，这条是双视图方案的唯一真相证明。
    """
    raw = raw or ""
    index_text = clean_for_index(raw)
    return {
        "text": index_text,                        # 索引版：检索 / RAG 入库
        "text_verbatim": clean_for_display(raw),   # 原文版：溯源展示 / 与客户对照
        "raw_char_count": len(raw),
        "char_count": len(index_text),
        "cleaned_ratio": round(len(index_text) / max(len(raw), 1), 3),
        "removed_control_chars": count_control_chars(raw),
    }


# 兼容旧调用点：旧名字等价于索引版（Day 5 之前的代码/手册用的是它）
clean_text = clean_for_index
