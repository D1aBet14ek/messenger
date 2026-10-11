# Резервная команда запуска (на случай, если Dokploy спросит именно Procfile).
# Порт 8000 — такой же, как в nixpacks.toml и в настройках домена Dokploy.
web: gunicorn app:app --bind 0.0.0.0:8000 --workers 2 --timeout 60 --access-logfile - --error-logfile -
