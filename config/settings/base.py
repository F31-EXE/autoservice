"""Base settings shared by all environments. Secrets come only from the environment."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    LLM_LOG_PAYLOADS=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)


def env_number(name: str, default: float, cast: type = float):
    """Numeric env var where an empty value (as in .env.example) means "use default"."""
    raw = env.str(name, default="").strip()
    return cast(raw) if raw else cast(default)


SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_celery_beat",
    "apps.common",
    "apps.accounts",
    "apps.customers",
    "apps.workorders",
    "apps.catalog",
    "apps.recommendations",
    "apps.llm",
    "apps.outreach",
    "apps.messaging",
    "apps.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context.station_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://vozvrat:vozvrat@localhost:5432/vozvrat")
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:today"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = env.path("MEDIA_ROOT", default=str(BASE_DIR / "media"))

# Uploaded exports hold personal data of many clients: delete files N days after parsing
IMPORT_FILE_RETENTION_DAYS = env_number("IMPORT_FILE_RETENTION_DAYS", 30, int)

# --- Celery ---
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TIMEZONE = "UTC"
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# --- LLM (Yandex AI Studio, OpenAI-compatible API) ---
LLM_BASE_URL = env("LLM_BASE_URL", default="https://llm.api.cloud.yandex.net/v1")
LLM_API_KEY = env("LLM_API_KEY", default="")
LLM_FOLDER_ID = env("LLM_FOLDER_ID", default="")
LLM_MODEL_EXTRACT = env("LLM_MODEL_EXTRACT", default="")
LLM_MODEL_INTENT = env("LLM_MODEL_INTENT", default="")
LLM_PRICE_PER_1K_INPUT_RUB = env_number("LLM_PRICE_PER_1K_INPUT_RUB", 0.0)
LLM_PRICE_PER_1K_OUTPUT_RUB = env_number("LLM_PRICE_PER_1K_OUTPUT_RUB", 0.0)
LLM_MONTHLY_BUDGET_RUB = env_number("LLM_MONTHLY_BUDGET_RUB", 3000.0)
LLM_CONCURRENCY = env_number("LLM_CONCURRENCY", 4, int)
LLM_LOG_PAYLOADS = env("LLM_LOG_PAYLOADS")
LLM_BACKEND = env("LLM_BACKEND", default="openai")  # openai | fake
LLM_TIMEOUT_SECONDS = env_number("LLM_TIMEOUT_SECONDS", 60.0)
LLM_EXTRACT_BATCH_SIZE = env_number("LLM_EXTRACT_BATCH_SIZE", 40, int)

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="vozvrat@localhost")

# --- Messaging ---
MESSAGE_BACKEND_SMS = env("MESSAGE_BACKEND_SMS", default="console")
SMS_API_KEY = env("SMS_API_KEY", default="")
SMS_SENDER_NAME = env("SMS_SENDER_NAME", default="")
SMS_INBOUND_SECRET = env("SMS_INBOUND_SECRET", default="")
SMS_MAX_SEGMENTS = env_number("SMS_MAX_SEGMENTS", 2, int)

# MAX messenger bot (https://dev.max.ru); the API base moved to platform-api2 in 2026
MESSAGE_BACKEND_MAX = env("MESSAGE_BACKEND_MAX", default="console")  # console | max
MAX_API_BASE_URL = env("MAX_API_BASE_URL", default="https://platform-api2.max.ru")
MAX_BOT_TOKEN = env("MAX_BOT_TOKEN", default="")
MAX_WEBHOOK_SECRET = env("MAX_WEBHOOK_SECRET", default="")
MAX_BOT_USERNAME = env("MAX_BOT_USERNAME", default="")
MAX_LINK_TEMPLATE = env("MAX_LINK_TEMPLATE", default="https://max.ru/{bot}?start={payload}")

# --- Logging: PII is scrubbed from every record as a safety net ---
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"pii": {"()": "apps.common.logging.PiiScrubFilter"}},
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "plain",
            "filters": ["pii"],
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}

# Static periodic tasks (synced into django-celery-beat on start). Times are UTC.
CELERY_BEAT_SCHEDULE = {
    "expire-recommendations": {
        "task": "apps.recommendations.tasks.expire_recommendations_task",
        "schedule": 60 * 60 * 24,
    },
    # runs hourly; builds each station's queue once a day after 07:00 local time (spec 7.4)
    "build-daily-queues": {
        "task": "apps.outreach.tasks.build_daily_queues_task",
        "schedule": 60 * 60,
    },
    "purge-import-files": {
        "task": "apps.workorders.tasks.purge_import_files_task",
        "schedule": 60 * 60 * 24,
    },
    # sends approved reminders when the station is inside its send window (10:00–20:00)
    "dispatch-approved-reminders": {
        "task": "apps.messaging.tasks.dispatch_due_task",
        "schedule": 10 * 60,
    },
}
