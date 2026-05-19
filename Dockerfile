# CTV Order Entry — production web image (Control Room)
# Python 3.12, uv, Tesseract OCR, pymssql (no Chrome — web UI uses SQL + HTTP, not Selenium)

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PORT=4000

WORKDIR /app

# Native deps: OCR (scanned PDFs), FreeTDS runtime (pymssql)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        freetds-bin \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install Python dependencies (production only)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code
COPY . .

# Writable dirs (also bind-mounted in compose for persistence)
RUN mkdir -p incoming processed errors data

EXPOSE 4000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/', timeout=3)"

CMD ["uv", "run", "python", "web_main.py"]
