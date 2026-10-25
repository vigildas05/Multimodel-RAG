import os

# Runs before any app.* module is imported, so it always precedes the first protobuf import
# (qdrant-client, transformers) regardless of import order. PaddlePaddle's generated
# protobuf modules need the pure-Python implementation; see services/ocr_service.py.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
