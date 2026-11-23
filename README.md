# doc-parser

一个把 **PDF / DOCX / TXT / XLSX** 解析成结构化文本的 FastAPI 服务：上传一个文件，拿回可检索的正文、逐页溯源和一份可审计的清洗指标。

面向的典型场景是 RAG / 文档检索的入库前置：先把文件变成干净、可对齐原文的文本，再交给向量化或全文索引。

## 特性

- 四种格式统一出口：PDF（逐页）、DOCX（段落）、TXT（编码自动探测）、XLSX（逐 sheet）
- **双视图**：`text` 供检索入库，`text_verbatim` 供溯源展示，两者可用不变式互相校验
- 上传安全：后缀白名单 → 边读边计大小（不把大文件读进内存）→ 解析，三步顺序固定
- 全链路 `request_id`：响应头、响应体、每一行日志都带同一个 rid
- 异常分层：对外是结构化 JSON + 人话提示，对内是带 `exc_info` 的完整堆栈
- 质量门：解析出空文本直接 422（而不是静默返回空字符串），清洗率过低打 WARNING
- 页数上限保护：超限截断并显式标记 `truncated`

## 快速开始

环境：Windows / macOS / Linux，Python 3.11。

```powershell
git clone <your-repo-url> doc-parser
cd doc-parser
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000
```

确认起来：

```powershell
curl.exe -s -w "`nHTTP=%{http_code}`n" http://127.0.0.1:8000/health
# {"status":"ok","service":"doc-parser","version":"0.1.0"}
# HTTP=200
```

上传一个文件（PowerShell 里必须用 `curl.exe`，且路径前要带 `@`）：

```powershell
curl.exe -s -X POST "http://127.0.0.1:8000/api/v1/documents/parse" `
  -F "file=@samples/sample.pdf" -o resp.json -w "HTTP=%{http_code} time=%{time_total}s`n"
```

`samples/` 里有四个可用的冒烟样本：`sample.pdf`（正常 PDF）、`sample.docx`（正常 Word）、`scanned.pdf`（无文本层的扫描件，用于验证 422）、`tmp_gbk.txt`（GBK 编码文本，用于验证编码探测）。

## API

### `POST /api/v1/documents/parse`

`multipart/form-data`，字段名 `file`。

| 查询参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `verbatim` | bool | `false` | 为 `true` 时额外返回原文版 `text_verbatim`。默认不返回，避免响应体翻倍（同一份 `sample.pdf` 实测 14.3KB → 21.6KB，只多一个字段，`text` 完全不变） |

成功响应（200，`samples/sample.pdf` 的真实输出，`pages` 只保留两条示意）：

```json
{
  "filename": "sample.pdf",
  "suffix": ".pdf",
  "size_bytes": 257758,
  "type": "pdf",
  "page_count": 6,
  "truncated": false,
  "pages": [
    { "page": 1, "text": "以下调用结果正确的是（A ）\nA. power(3) 返回 9..." },
    { "page": 2, "text": "..." }
  ],
  "text": "以下调用结果正确的是(A )\nA. power(3) 返回 9...",
  "raw_char_count": 4434,
  "char_count": 4028,
  "cleaned_ratio": 0.908,
  "removed_control_chars": 0
}
```

对比 `pages[0].text` 与 `text` 开头能直接看出双视图的差别：原文是 `（A ）`（全角括号 + 空格），索引版归一成了 `(A )`。

| 字段 | 含义 |
|---|---|
| `filename` / `suffix` / `size_bytes` | 原始文件名、后缀、实际字节数（服务端边读边累计，不是客户端声明值） |
| `type` | `pdf` / `docx` / `txt` / `xlsx` |
| `page_count` | PDF 的真实总页数（即使被截断也报真实值） |
| `truncated` | `page_count > max_pdf_pages` 时为 `true`，表示只解析了前 `max_pdf_pages` 页 |
| `pages` | PDF 专用，逐页文本，最多 `max_pdf_pages` 条 |
| `text` | **索引版**文本：NFC 后再 NFKC 归一、空白压缩、去空行，供检索 / 向量入库 |
| `text_verbatim` | **原文版**文本：只做 NFC + 删控制字符 + 统一换行，不做任何改字操作；默认不返回 |
| `raw_char_count` / `char_count` | 原文长度 / 索引版长度 |
| `cleaned_ratio` | `char_count / raw_char_count`，保留三位小数 |
| `removed_control_chars` | 原文里被删掉的控制字符个数（审计用） |

两份文本满足不变式 `clean_for_index(text_verbatim) == text`，即“两件衣服裹的是同一份内容”。需要与客户原文逐字对照时用 `text_verbatim`，需要召回时用 `text`。

上表是 PDF 的输出，其余三种格式各自多带一组结构字段：

| 格式 | 额外字段 |
|---|---|
| `txt` | `encoding`：实际使用的编码（`utf-8` / `utf-8-sig` / `gb18030` / `utf-16` / `utf-8(replace)`） |
| `docx` | `block_count`、`blocks[]`（`type` 为 `heading` / `paragraph` / `table`，表格带 `table_index` 与 `rows`）、`known_limitation` |
| `xlsx` | `sheet_count`、`sheets{sheet 名: {shape, columns, rows}}` |

DOCX 的 `blocks` 是按**类型分组**读取（先全部段落、再全部表格），不是文档的真实顺序；表格内容只进 `blocks`，不进 `text`。这样设计的原因是“按顺序解析”在 python-docx 里需要遍历底层 XML，代价与收益不成比例，而表格单独结构化后前端反而更好用。

错误响应统一结构：

```json
{
  "code": "FILE_TOO_LARGE",
  "message": "文件超过 50MB 限制",
  "details": { "limit_mb": 50 },
  "request_id": "1dc643644ef6"
}
```

| HTTP | `code` | 触发条件 |
|---|---|---|
| 415 | `FILE_TYPE_NOT_ALLOWED` | 后缀不在白名单（读文件之前就拒，省 IO） |
| 413 | `FILE_TOO_LARGE` | 边读边累计超过 `max_file_size_mb`（不落盘、不解析） |
| 422 | `PARSE_FAILED` | 解析后无有效文本（纯控制字符 / 全空白 / 扫描件）、文件损坏、非真实 Office 包 |
| 500 | `INTERNAL_ERROR` | 未预料异常；堆栈只进日志，响应里只给 `request_id` |

`422` 的 `details` 会带具体依据，例如 `samples/scanned.pdf` 的实测响应是 `{"empty_page_ratio": 1.0, "page_count": 1}`、损坏 PDF 是 `{"reason": "FileDataError", "hint": "..."}`。

### `GET /health`

```json
{ "status": "ok", "service": "doc-parser", "version": "0.1.0" }
```

## 配置

`config/config.yaml`：

```yaml
name: doc-parser
version: "0.1.0"
debug: false
log_dir: logs
log_level: INFO
max_file_size_mb: 50
allowed_extensions: [".pdf", ".docx", ".txt", ".xlsx"]
max_pdf_pages: 200
```

环境变量可临时覆盖：`APP_LOG_LEVEL=DEBUG`、`APP_DEBUG=1`。

三条必须知道的行为（都踩过坑，见 `故障演练记录.md`）：

1. **改 YAML 必须重启**。`uvicorn --reload` 只监听 `*.py`，不会因为改了 YAML 而重载；`max_file_size_mb` 甚至在 `documents.py` 里被算成模块级常量 `MAX_BYTES`，import 时只算一次。
2. **配置文件不存在时不会崩**，会用 `AppConfig` 的默认值继续跑——注意默认 `debug: true`，意味着未捕获异常会把堆栈回给客户端。生产部署应显式提供配置文件。
3. `log_dir` 写相对路径（如 `logs`）时基于项目根目录；写绝对路径（如 `Z:\logs`）会整体覆盖基准目录。目录建不出来时服务**启动即失败**（fail-fast，不静默丢日志）。

## 目录结构

```
doc-parser/
├── app/
│   ├── main.py              # 应用组装：中间件、异常处理、/health、挂路由
│   ├── api/
│   │   └── documents.py     # 接口层：白名单 → 流式限大小 → 调服务 → 临时文件清理
│   ├── services/
│   │   ├── parsers.py       # 解析层：四种格式分发、逐页/逐 sheet、页数上限
│   │   └── cleaner.py       # 清洗层：索引版 / 原文版双视图 + 可审计计数
│   ├── core/
│   │   ├── config.py        # 配置加载（YAML + 环境变量覆盖 + 默认值兜底）
│   │   ├── errors.py        # 异常基类与错误码 → HTTP 状态映射
│   │   └── logger.py        # 日志：控制台 + 按天切割文件 + rid 注入
│   └── utils/
│       └── retry.py         # 重试装饰器（指数退避 + 可配置异常类型）
├── config/config.yaml
├── samples/                 # 冒烟样本（正常 / 扫描件 / GBK 文本）
├── tests/                   # 12 个用例：解析器 + 重试
├── 故障演练记录.md           # 6 条人为故障的复现与加固记录
└── requirements.txt
```

分层原则：`api` 只处理 HTTP（读流、校验大小、临时目录），`services` 只处理格式与文本质量，`core` 提供配置 / 日志 / 异常这些横切能力。接口层不关心 PDF 怎么解析，服务层不关心 HTTP 状态码。

## 测试

```powershell
python -m pytest tests -q
# 12 passed in ~1.2s
```

覆盖：TXT 的 GBK 解码不花屏、PDF 正常路径、DOCX 段落与表格、控制字符与全角空格清洗、清洗率计算、非法后缀拒绝（6 条）；重试装饰器的“瞬时失败后成功 / 耗尽后抛最后异常 / 不吞未注册异常 / 首次成功不 sleep / 退避上限 / 保留函数元数据”（6 条）。

双视图不变式 `clean_for_index(text_verbatim) == text` 与“空文本 → 422”目前靠接口实测验证（见 `故障演练记录.md`），尚未固化成自动化用例——这是下一步要补的。

## 设计取舍

**为什么两套文本，而不是一套**

NFKC 归一能让检索更准（全角标点折半角、`①`→`1`、`ﬁ`→`fi`），但同一步会让原文变样，客户拿原文对照时对不上。所以拆成 `text`（索引版，允许改字）与 `text_verbatim`（原文版，只删不可见控制字符），再用 `clean_for_index(text_verbatim) == text` 作为两者同源的证明。

**为什么边读边判大小**

`UploadFile` 整个读进内存再判断大小，等于把内存交给客户端。实现是每次读 1MB、累加后立刻判上限：超限时抛出发生在写盘之前，所以既不落盘也不解析，临时目录照样被 `finally` 清掉。

**为什么 `pages` 有上限且单独标记 `truncated`**

一份 500 页的合同可能拖死单机服务。上限 200 页，`page_count` 仍报真实值、`truncated` 显式标记，调用方能自己决定“要不要提示用户只看了一部分”。注意判断是**严格大于**，所以 200 页的 PDF 永远返回 `truncated: false`。

**为什么异常要分两层**

客户看到的是“PDF 文件无法打开（可能已损坏或受密码保护）”，日志里是 pymupdf 的原始异常 + `exc_info` 堆栈。库的英文报错透给客户，等于把内部实现和英文错误码一起暴露；只给技术细节又没法排查。所以对外人话 + `details{reason,hint}`，对内完整堆栈。

**为什么 rid 用 contextvars 而不是层层传参**

`request_id` 需要出现在 service、cleaner、第三方库（httpx）的每一行日志里。用 `ContextVar` + 挂在 handler 上的 `Filter`，任何地方 `logger.info` 都自动带 rid，不用把它作为参数穿过五层函数。

## 已知限制

如实列出，也是下一步的改进清单（详细复现见 `故障演练记录.md`）：

- **配置缺失时静默降级**：`debug` 默认 `true`，配置被删后服务照常启动、`/health` 仍 `ok`，外部看不出异常
- **数值配置无下限校验**：`max_file_size_mb: 0` 会让所有上传 413，但服务照常启动
- **`MAX_BYTES` 在 import 时计算**：改限额必须重启，热改不生效
- **`rmtree(ignore_errors=True)` 无日志**：临时目录删不掉时不会留下任何痕迹
- **端口冲突在启动生命周期之后才发现**：将来若在启动阶段加载模型/连库，会白跑一遍
- **单机、无鉴权、无并发限制**：解析在事件循环里同步执行，大文件会阻塞其他请求；生产化需要线程池 / 任务队列
- **无 OCR**：扫描件（无文本层）目前只返回 422 并给出 `empty_page_ratio`
- **DOCX 顺序失真**：段落与表格按类型分组返回，不是文档真实顺序；表格内容不进 `text`（详见 API 一节）

## 故障演练记录

`故障演练记录.md` 记录了 6 条人为制造的故障（日志目录不可用、体积上限为 0、配置被删、超页数 PDF、连传 20 个文件、端口被占用），每条都有现象 → 原因 → 排查 → 处理 → 加固，以及真实日志与 `request_id` 作为证据。

其中两条结论对部署更有参考价值：

- 同一类“配置不可用”会走向两个极端：日志目录不可用是**启动即死**（fail-fast），配置文件被删是**静默降级**（更危险）
- Windows 上 `::`（IPv6 通配）与 `127.0.0.1` 可以同时监听同一端口，做“端口被占用”验证时必须绑同一地址族
