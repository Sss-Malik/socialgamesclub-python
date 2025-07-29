FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# System deps for Playwright
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
       libnss3 libatk1.0-0 libatk-bridge2.0-0 libx11-xcb1 libxcomposite1 \
       libxdamage1 libxrandr2 libgbm1 libgtk-3-0 libasound2 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Pre-copy for layer cache
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && playwright install-deps \
    && playwright install \
    && rm -rf /root/.cache/pip


# Install Playwright *as the correct user*
RUN playwright install

# Copy source code
COPY . .

EXPOSE 8000

ENV PYTHONPATH=/app

CMD ["uvicorn", "api.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "4", "--log-level", "info"]
