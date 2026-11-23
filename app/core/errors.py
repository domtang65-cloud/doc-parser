'''
异常层，统一处理项目中出现的异常
带错误码和HTTP状态码
'''
class AppError(Exception):
    code : str = 'APP_ERROR'
    http_status : int = 500

    def __init__(self, message : str ,details : dict | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)

class FileTypeNotAllowed(AppError):
    code = 'FILE_TYPE_NOT_ALLOWED'
    http_status  = 415

class FileTooLarge(AppError):
    code = 'FILE_TOO_LARGE'
    http_status  = 413

class ParseFailed(AppError):
    code = 'PARSE_FAILED'
    http_status  = 422

class NotFound(AppError):
    code = 'NOT_FOUND'
    http_status  = 404
