FROM python:3.10-slim

# Small least-squares fits must not monopolize all ARM cores during capture.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR /app

# Runtime/system deps:
# - git: needed for scansegmentapi installed from GitHub
# - libgl1/libglib2.0-0/libgomp1: common runtime libs for opencv/scipy stacks
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    gcc \
    python3-dev \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

# Keep certificate verification enabled for both pip and the Git dependency.
# Print pip's detailed file log on failure; failed layers cannot be inspected later.
RUN python -m pip install --verbose --log /tmp/pip-upgrade.log --upgrade pip \
    || { status=$?; cat /tmp/pip-upgrade.log; exit "$status"; }

RUN python -m pip install --verbose --log /tmp/pip-install.log -r requirements.txt \
    || { status=$?; cat /tmp/pip-install.log; exit "$status"; }

RUN python -m pip check && rm -f /tmp/pip-upgrade.log /tmp/pip-install.log

COPY . .

RUN mkdir -p /app/logs /app/data

# API + up to 4 UDP listeners for picoscan streams
EXPOSE 8000/tcp
EXPOSE 2115/udp
EXPOSE 2116/udp
EXPOSE 2117/udp
EXPOSE 2118/udp

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

