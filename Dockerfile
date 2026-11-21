FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY . .
# a Windows checkout may have CRLF line endings: normalize shell scripts for Linux
RUN sed -i 's/\r$//' docker/*.sh

RUN useradd --create-home app && mkdir -p /app/media /app/staticfiles && chown -R app /app
USER app

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
