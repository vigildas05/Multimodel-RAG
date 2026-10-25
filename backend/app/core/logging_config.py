import logging

from app.core.middleware import RequestIdLogFilter


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s [%(request_id)s]: %(message)s",
    )
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RequestIdLogFilter) for f in handler.filters):
            handler.addFilter(RequestIdLogFilter())
