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
    ca-certificates \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

ARG GIT_SSL_NO_VERIFY=0
ARG PIP_TRUSTED_HOSTS=""

RUN if [ "${GIT_SSL_NO_VERIFY}" = "1" ]; then git config --global http.sslVerify false; fi \
    && PIP_TRUSTED_FLAGS="" \
    && for h in ${PIP_TRUSTED_HOSTS}; do PIP_TRUSTED_FLAGS="${PIP_TRUSTED_FLAGS} --trusted-host ${h}"; done \
    && pip install --upgrade pip ${PIP_TRUSTED_FLAGS} \
    && pip install -r requirements.txt ${PIP_TRUSTED_FLAGS}

COPY . .

RUN mkdir -p /app/logs /app/data

# API + up to 4 UDP listeners for picoscan streams
EXPOSE 8000/tcp
EXPOSE 2115/udp
EXPOSE 2116/udp
EXPOSE 2117/udp
EXPOSE 2118/udp

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

