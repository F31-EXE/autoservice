from .base import *

DEBUG = env.bool("DJANGO_DEBUG", default=True)
SECRET_KEY = SECRET_KEY or "dev-insecure-secret-key-do-not-use-in-prod"
ALLOWED_HOSTS = ALLOWED_HOSTS or ["localhost", "127.0.0.1", "0.0.0.0"]  # noqa: S104 - dev only, inside docker
