FROM python:3.11-slim

# Install Chrome dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    chromium \
    chromium-driver \
    libnss3 \
    libfontconfig1 \
    libxss1 \
    libasound2 \
    libxrandr2 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxi6 \
    libxtst6 \
    libpangocairo-1.0-0 \
    libatk1.0-0 \
    libcairo-gobject2 \
    libgtk-3-0 \
    libgdk-pixbuf-2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables for Chrome
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy application files
COPY . .

# Expose port (Railway/Heroku will set PORT env var)
# Note: server.py defaults to 5001 for local development (see server.py line 229)
# but production uses PORT env var or defaults to 5000
EXPOSE 5000

# Use gunicorn for production
CMD gunicorn server:app --bind 0.0.0.0:${PORT:-5000} --log-file -
