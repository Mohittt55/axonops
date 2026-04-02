FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc && rm -rf /var/lib/apt/lists/*

COPY axonops_sdk/ ./axonops_sdk/
RUN pip install --no-cache-dir ./axonops_sdk

COPY plugins/ ./plugins/
RUN pip install --no-cache-dir \
    ./plugins/psutil \
    ./plugins/docker \
    ./plugins/prometheus \
    ./plugins/kubernetes \
    ./plugins/aws

COPY axonops_core/ ./axonops_core/
RUN pip install --no-cache-dir ./axonops_core fastapi "uvicorn[standard]" websockets

RUN mkdir -p /app/data
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["python", "-m", "axonops_core.cli", "--host", "0.0.0.0", "--port", "8000"]
