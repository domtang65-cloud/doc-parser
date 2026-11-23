"""应用入口：组装应用、注册中间件与异常处理"""
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.errors import AppError, ParseFailed
from app.core.logger import setup_logging, request_id_var

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.name, version=settings.version, debug=settings.debug)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
    request.state.request_id = rid
    # 写入 contextvars：Formatter 会从这里取，所有后续日志行自动带 rid，
    # 不用把 rid 当参数一层层传给 service / cleaner。
    # 不在 finally 里 reset：每个 HTTP 请求跑在独立 task 里，task 结束上下文自动销毁；
    # 而 500 兜底 handler 运行在中间件外层，提前 reset 会让它丢掉 rid。
    request_id_var.set(rid)
    logger.info("→ %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.info("← %s %s 500(%s)",
                    request.method, request.url.path, type(exc).__name__)
        raise          # 关键：抛回去，让外层 handler 生成干净的 500 JSON
    else:
        response.headers["X-Request-Id"] = rid
        logger.info("← %s %s %s",
                    request.method, request.url.path, response.status_code)
        return response


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError):
    """业务异常：转成统一 JSON 结构，不让堆栈泄露给客户"""
    rid = getattr(request.state, "request_id", "-")
    logger.warning("业务异常 %s: %s", exc.code, exc.message)
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
            "request_id": rid,
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception):
    """兜底异常：未预料的错误也要有结构，同时把完整堆栈记进日志"""
    rid = getattr(request.state, "request_id", "-")
    logger.exception("未捕获异常")
    response = JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "服务内部错误，请联系管理员并提供 request_id",
            "request_id": rid,
        },
    )
    # 500 响应由最外层 ServerErrorMiddleware 生成，不经过中间件的 else 分支，
    # 所以必须在这里手动补上响应头，否则客户端拿不到 X-Request-Id
    response.headers["X-Request-Id"] = rid
    return response


@app.get("/health")
def health():
    return {"status": "ok", "service": settings.name, "version": settings.version}


from app.api.documents import router as documents_router
app.include_router(documents_router)