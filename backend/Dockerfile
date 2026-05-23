# Playwright base image already provides Chromium + every system lib it
# needs. We pin to the same version as the playwright Python package to
# guarantee browser/binding compatibility.
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/root/.cache/huggingface \
    TRANSFORMERS_NO_ADVISORY_WARNINGS=1 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

# --- Layer 1: pip + torch CPU. Rarely changes, big to download. ---
RUN pip install --upgrade pip \
 && pip install --extra-index-url https://download.pytorch.org/whl/cpu \
        torch==2.4.1

# --- Layer 2: Python dependencies. Re-runs only when requirements.txt changes. ---
COPY requirements.txt .
RUN pip install -r requirements.txt

# --- Layer 3: pre-warm the HuggingFace models so first request stays fast. ---
RUN python -c "from sentence_transformers import CrossEncoder, SentenceTransformer; \
    CrossEncoder('cross-encoder/nli-deberta-v3-xsmall'); \
    SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# --- Layer 4: app source. Cheapest layer, changes most often. ---
COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
