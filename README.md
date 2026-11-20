# Vozvrat

Сервис возврата клиентов автосервиса: загружает выгрузки заказ-нарядов, находит
рекомендации мастеров (правилами и LLM), планирует напоминания, отправляет SMS и сообщения
в MAX после подтверждения администратором, понимает ответы и считает возвращённую выручку
с контрольной группой. Решения и допущения — `docs/decisions.md`.

## Запуск в Docker

```bash
cp .env.example .env          # заполнить секреты; для демо без ключей: LLM_BACKEND=fake
docker compose up --build     # migrate, web :8000, worker, beat, postgres, redis
docker compose exec web python manage.py createsuperuser
```

Кабинет — http://localhost:8000/, Django admin — `/admin/` (только суперадмин). Организацию,
точку и владельца создаёт суперадмин в админке; дальше владелец работает в «Настройках».

### Посмотреть на демо-данных

```bash
docker compose exec web python manage.py load_demo
```

Создаёт «Демо-сеть автосервисов» с 80 выдуманными клиентами и прогоняет их через весь
конвейер: две выгрузки, правила и извлечение, кампанию 25 дней назад, ответы клиентов,
визиты и атрибуцию, очередь на сегодня. Вход: владелец `demo` / `demo12345` (видит
«Результаты» и «Настройки»), администратор `manager` / `demo12345`. Внешние сервисы не
вызываются (LLM — офлайн-эвристика, сообщения — в лог). Повторный запуск пересоздаёт демо,
`--remove` удаляет; при `DJANGO_DEBUG=False` команда откажется без `--force`.

## Локальная разработка

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # нужны PostgreSQL 16 и Redis
python manage.py migrate
pytest                        # реальные внешние API в тестах не вызываются
ruff check . && ruff format --check .
```

## Полезные команды

| Команда | Что делает |
| --- | --- |
| `python manage.py seed_catalog` | Обновить справочник работ и глобальные синонимы |
| `python manage.py catalog_coverage --station N` | Какие строки ЗН не сопоставились со справочником |
| `python manage.py eval_extraction --golden tests/golden/extraction.jsonl --model <id\|mock>` | Качество LLM-извлечения: precision/recall/F1, стоимость |
| `python manage.py build_queue --station N [--date YYYY-MM-DD]` | Собрать очередь напоминаний вручную |
| `python manage.py max_setup_webhook --url https://<host>/messaging/max/webhook/` | Подписать бота MAX на вебхук |
| `python manage.py delete_client_data --client-id N [--yes]` | Удалить все данные клиента по запросу |
| `python manage.py load_demo [--clients 80] [--remove]` | Демо-данные для показа кабинета |

## Периодические задачи (Celery beat)

- каждый час — очередь напоминаний на сегодня (после 07:00 по времени точки, один раз в день);
- каждые 10 минут — отправка одобренных напоминаний в окне отправки;
- ежедневно — истечение рекомендаций по сроку жизни и удаление старых файлов выгрузок.

## Интеграции

- **LLM**: Yandex AI Studio (OpenAI-совместимый API), `LLM_*` в `.env`; без ключа извлечение
  ждёт, `LLM_BACKEND=fake` — офлайн-эвристика для демо.
- **MAX**: бот создаётся через https://max.ru/masterbot; `MAX_*` в `.env`, `MESSAGE_BACKEND_MAX=max`.
- **SMS**: провайдер ещё не выбран — сейчас `MESSAGE_BACKEND_SMS=console` (сообщения пишутся в лог
  без ПДн). Входящие SMS принимает `/messaging/sms/inbound/` (секрет `SMS_INBOUND_SECRET`).
