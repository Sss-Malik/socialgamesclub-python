FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# System deps for Playwright
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential libpq-dev curl gnupg2 ca-certificates wget \
      libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
      libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
      libgtk-3-0 libgtk-4-1 libasound2 libpangocairo-1.0-0 \
      libxss1 libxtst6 libxext6 libpci3 libdrm2 libdbus-1-3 libXScrnSaver1 \
      fonts-liberation libwoff2-1 libopus0 libvpx7 \
      libwebpdemux2 libwebpmux3 libavif15 libharfbuzz-icu0 \
      libenchant-2-2 libsecret-1-0 libhyphen0 libpsl5 libnghttp2-14 \
      libegl1-mesa libgles2-mesa libx264-155 \
      libgstreamer1.0-0 gstreamer1.0-plugins-base \
      gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
      gstreamer1.0-plugins-ugly gstreamer1.0-libav \
      gstreamer1.0-alsa libflite1 libgraphene-1.0-1 \
      libatomic1 libxslt1.1 libevent-2.1-7 libmanette-0.2-0 && \
    rm -rf /var/lib/apt/lists/*

# Pre-copy for layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Install Playwright *as the correct user*
RUN playwright install

# Copy source code
COPY . .

EXPOSE 8000

ENV PYTHONPATH=/app

CMD ["uvicorn", "api.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "4", "--log-level", "info"]
