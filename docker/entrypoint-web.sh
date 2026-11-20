#!/bin/sh
set -e
python manage.py collectstatic --noinput -v0
if [ "${DJANGO_DEBUG:-False}" = "True" ]; then
  exec python manage.py runserver 0.0.0.0:8000
fi
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3
