# Vozvrat

Сервис возврата клиентов автосервиса (MVP). ТЗ — см. задачу; решения и допущения — `docs/decisions.md`.

## Запуск в Docker

```bash
cp .env.example .env
docker compose up --build
docker compose exec web python manage.py createsuperuser
```

Кабинет: http://localhost:8000/, Django admin: http://localhost:8000/admin/.

## Локальная разработка

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # нужны PostgreSQL 16 и Redis
python manage.py migrate
pytest
ruff check . && ruff format --check .
```
