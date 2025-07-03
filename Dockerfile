FROM python:3.10-slim
WORKDIR /app

# system deps for playwright if needed
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
       libnss3 libatk1.0-0 libatk-bridge2.0-0 libx11-xcb1 libxcomposite1 \
       libxdamage1 libxrandr2 libgbm1 libgtk-3-0 libasound2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Preinstall playwright browsers (if using)
RUN playwright install --with-deps

EXPOSE 8000

CMD ["uvicorn", "api.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "4", "--log-level", "info"]
