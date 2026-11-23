"""日志层：控制台 + 文件双输出，按天切割，保留 14 天，全链路自动注入 request_id"""
import contextvars
import logging
import sys
import time
from logging.handlers import TimedRotatingFileHandler

from app.core.config import BASE_DIR, settings

# 每个请求一个值。contextvars 的关键性质：async 任务切换时自动隔离，
# 同一请求内跨函数/跨 await 调用都能读到，不需要把 rid 当参数层层传递。
# 默认值 "-"：独立脚本（如 tmp_verify.py）没有 HTTP 请求上下文时也能正常格式化。
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class RequestIdFilter(logging.Filter):
    """给每条 LogRecord 补上 request_id 属性，供 Formatter 的 %(request_id)s 使用。

    挂在 handler 上而不是 logger 上：这样连第三方库（httpx 等）经 root 输出的日志
    也会被注入，且没有请求上下文时自动落 "-" 而不是抛 KeyError。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get("-")
        return True


_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | [rid=%(request_id)s] | %(name)s | "
    "%(filename)s:%(lineno)d | %(message)s"
)


def setup_logging() -> None:
    """在应用启动时调用一次。重复调用会直接返回，避免日志重复打印"""
    root = logging.getLogger()
    if root.handlers:  # 已经有 handler，说明初始化过了
        return

    root.setLevel(settings.log_level)
    formatter = logging.Formatter(_LOG_FORMAT)
    rid_filter = RequestIdFilter()

    # 1) 控制台：开发时眼睛盯这个
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(rid_filter)
    root.addHandler(console)

    # 2) 文件：客户现场唯一的事故现场记录
    log_dir = BASE_DIR / settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        filename=log_dir / "app.log",
        when="midnight",     # 每天零点切一个新文件
        backupCount=14,      # 只留 14 天，防止磁盘写满（客户现场真会因为这个挂）
        encoding="utf-8",    # 必须指定，否则中文日志在 Windows 上是乱码
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(rid_filter)
    root.addHandler(file_handler)

    # 跨天启动补一次切割：handler 的 shouldRollover 只在 emit()（写日志时）被调用，
    # 昨晚停服、今天首条日志迟迟不来时，app.log 会一直挂着昨天的日期。
    # 用 handler 自带的 doRollover() 而不是手动 os.rename：
    # ① 备份文件名由它自己按 suffix 算，和 backupCount 的清理正则一致；
    # ② 内部有 os.path.exists(dfn) 判断，重复启动不会覆盖已有备份；
    # ③ 切完会自动把 rolloverAt 推进到下一个午夜，不影响后续正常切割。
    if time.time() >= file_handler.rolloverAt:
        file_handler.doRollover()
