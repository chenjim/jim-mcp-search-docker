FROM python:3.12-slim

WORKDIR /app

# Allow proxy configuration during build (host network mode is used for building)
ARG HTTP_PROXY
ARG HTTPS_PROXY
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY}

# Install dependencies first for layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy source
COPY src /app/src

EXPOSE 17492

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "17492"]
