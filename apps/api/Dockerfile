# 1) Base image: Python 3.11 (inside the container)
FROM python:3.11-slim

# 2) Speed + safety: don't write .pyc files and show logs immediately.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 3) Set a working directory inside the image
WORKDIR /app

# 4) Create an unprivileged user to run the application, containers should not be run as root
# 'addUser' creates a minimal user without a password
RUN adduser --disabled-password --gecos "" app

# 5) Copy dependency list first to leverage Docker layer caching
COPY requirements.txt .

# 6) Install python dependencies inside the image.
# --no-cache-dir keeps the layer small; we don't need the cache after installation
RUN pip install --no-cache-dir -r requirements.txt

# 7) Copy the application source code into the image.
COPY . .

# 8) Drop priveleges to the unprivileged user: run the app as the 'app' user
USER app

# 9) Expose the port the app runs on
EXPOSE 8000

# 10) Container start command: run Uvicorn (FastAPI server) on container start
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]