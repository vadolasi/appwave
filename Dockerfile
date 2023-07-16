FROM python:3.11.4-slim-bookworm

RUN apt-get update && \
    apt-get -qy full-upgrade && \
    apt-get install -qy --no-install-recommends \
    curl \
    build-essential \
    libpq-dev && \
    rm -rf /var/lib/apt/lists/* && \
    curl -sSL https://get.docker.com/ | sh

WORKDIR /app

COPY pyproject.toml poetry.lock prisma/ /app/

RUN pip install poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-dev --no-interaction --no-ansi && \
    prisma generate

COPY . /app/

ENV ENV=production
EXPOSE 80

CMD python app/main.py
