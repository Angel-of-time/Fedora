# Use a lightweight Python image
FROM python:3.10-slim

# 1. Install Tesseract OCR and system libraries
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

# 2. Set working directory
WORKDIR /app

# 3. Copy dependencies and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy the rest of the application code
COPY . .

# 5. Command to run the app using Gunicorn
# Render automatically sets the $PORT environment variable
CMD gunicorn app:app --bind 0.0.0.0:$PORT
