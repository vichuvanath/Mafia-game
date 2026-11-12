"""Standard error schemas."""

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Standard unified error response format."""

    code: str
    message: str
    request_id: str
