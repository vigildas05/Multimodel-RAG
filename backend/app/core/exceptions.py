class AppError(Exception):
    """Base class for all application errors that should be reported to the client."""

    status_code: int = 500
    error_type: str = "app_error"

    def __init__(self, message: str, detail: str | None = None):
        self.message = message
        self.detail = detail
        super().__init__(message)


class InvalidFileTypeError(AppError):
    status_code = 400
    error_type = "invalid_file_type"


class FileTooLargeError(AppError):
    status_code = 400
    error_type = "file_too_large"


class EmptyDocumentError(AppError):
    status_code = 422
    error_type = "empty_document"


class OCRFailureError(AppError):
    status_code = 502
    error_type = "ocr_failure"


class TranscriptionError(AppError):
    status_code = 502
    error_type = "transcription_error"


class EmbeddingError(AppError):
    status_code = 500
    error_type = "embedding_error"


class VectorStoreUnavailableError(AppError):
    status_code = 503
    error_type = "vector_store_unavailable"


class LLMProviderError(AppError):
    status_code = 502
    error_type = "llm_provider_error"


class NoRelevantResultsError(AppError):
    status_code = 404
    error_type = "no_relevant_results"


class ContextTooLargeError(AppError):
    status_code = 422
    error_type = "context_too_large"


class ConfigurationError(AppError):
    status_code = 500
    error_type = "configuration_error"


class DocumentNotFoundError(AppError):
    status_code = 404
    error_type = "document_not_found"
