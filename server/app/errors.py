class AppError(Exception):
    def __init__(self, code, message, status=400, details=None):
        self.code, self.message, self.status, self.details = (
            code,
            message,
            status,
            details,
        )
        super().__init__(message)


def error_body(code, message, details=None):
    return {"error": {"code": code, "message": message, "details": details}}
