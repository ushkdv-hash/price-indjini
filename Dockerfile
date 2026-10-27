# syntax=docker/dockerfile:1
#
# Калькулятор стоимости продукции — контейнер для Apploy.
# Приложение то же, что в release/ (Amvera), меняется только упаковка.

FROM python:3.12-slim

# tzdata нужен, чтобы даты в истории и статистике были по Москве,
# а не по UTC. Без него TZ внутри slim-образа игнорируется.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Moscow \
    DATA_DIR=/data

WORKDIR /app

# Сначала зависимости: слой кешируется и не пересобирается при правках кода.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data

EXPOSE 80

# Запускаем через sh: права на исполнение файла внутри образа не важны.
ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint.sh"]
