"""模型调用错误。

retryable 决定适配层是否自动重试：鉴权失败和参数错误重试没有意义，
限流、服务端异常和网络故障才值得重试。
"""


class LLMError(RuntimeError):
    retryable = False

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMAuthError(LLMError):
    """密钥无效或无权限。"""


class LLMRequestError(LLMError):
    """请求参数、模型名等客户端错误。"""


class LLMRateLimitError(LLMError):
    retryable = True


class LLMServerError(LLMError):
    retryable = True


class LLMConnectionError(LLMError):
    retryable = True


class LLMResponseError(LLMError):
    """返回内容无法解析。"""
