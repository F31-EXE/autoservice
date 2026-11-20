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

# --- Messaging ---
MESSAGE_BACKEND_SMS = env("MESSAGE_BACKEND_SMS", default="console")
SMS_API_KEY = env("SMS_API_KEY", default="")
SMS_SENDER_NAME = env("SMS_SENDER_NAME", default="")
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", default="")
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", default="")

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
