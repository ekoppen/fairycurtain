FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends bluez libglib2.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY fairycurtain ./fairycurtain
RUN pip install --no-cache-dir .
ENV FAIRY_CONFIG=/config/config.yaml
ENV FAIRY_HOST=0.0.0.0
EXPOSE 8321
CMD ["fairycurtain"]
