FROM python:3.11-slim

# Install system dependencies that Chromium needs
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libexpat1 \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy ONLY requirements first — this layer is cached as long as
# requirements.txt doesn't change, so pip install and playwright
# install won't re-run when you edit .py files.
COPY requirements.txt .

RUN pip install -r requirements.txt

# Download Chromium once. This layer is cached independently of your code.
RUN playwright install chromium

# Now copy your actual code. Changes here only invalidate this layer
# and below — Chromium stays cached.
COPY . .

# Expose the signal server port
EXPOSE 5000

ENV PYTHONUNBUFFERED=1
CMD ["python", "bot.py"]
