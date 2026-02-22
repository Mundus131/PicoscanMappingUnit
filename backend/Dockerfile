FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Runtime/system deps:
# - git: needed for scansegmentapi installed from GitHub
# - libgl1/libglib2.0-0/libgomp1: common runtime libs for opencv/scipy stacks
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

# open3d wheels are often unavailable on arm64; skip in container build.
# Auto-calibration endpoint already handles missing open3d with clear error.
RUN grep -v "^open3d==" requirements.txt > requirements.docker.txt \
    && pip install --upgrade pip \
    && pip install -r requirements.docker.txt

COPY . .

RUN mkdir -p /app/logs /app/data

# API + up to 4 UDP listeners for picoscan streams
EXPOSE 8000/tcp
EXPOSE 2115/udp
EXPOSE 2116/udp
EXPOSE 2117/udp
EXPOSE 2118/udp

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

