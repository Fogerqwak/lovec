#!/usr/bin/env bash
# Lovec: налаштування сервера одною командою.  bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"

IP=$(curl -s4 ifconfig.me || hostname -I | awk '{print $1}')
DOMAIN="${1:-lovec.${IP}.sslip.io}"
echo "▶ IP: $IP   адреса: https://$DOMAIN"

# .env
[ -f .env ] || cp .env.example .env
set_env() { grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
set_env PUBLIC_URL "https://$DOMAIN"
ask() {  # питає лише якщо значення порожнє
  local cur; cur=$(grep "^$1=" .env | cut -d= -f2- || true)
  if [ -z "$cur" ] || [ "$cur" = "change-me-please" ]; then read -rp "  $2: " v; set_env "$1" "$v"; fi
}
ask TELEGRAM_BOT_TOKEN "Токен бота від @BotFather"
ask TELEGRAM_CHAT_ID   "Твій chat_id (з @userinfobot)"
ask DASHBOARD_PASSWORD "Пароль для входу з браузера"

# Caddy
printf '%s {\n\treverse_proxy lovec:8000\n}\n' "$DOMAIN" > Caddyfile

# файрвол
if command -v ufw >/dev/null && ufw status | grep -q active; then ufw allow 80,443/tcp >/dev/null; fi

# запуск
docker compose -f docker-compose.shared.yml down 2>/dev/null || true
docker compose up -d --build
echo "▶ Чекаю на сертифікат…"
for i in $(seq 1 30); do
  if curl -sf "https://$DOMAIN/health" >/dev/null 2>&1; then
    echo "✅ Готово: https://$DOMAIN"
    echo "   Напиши боту /start — з'явиться кнопка «Lovec»."
    echo "▶ Перевірка сайтів (1–2 хв):"
    docker compose exec -T lovec python -m lovec check || true
    exit 0
  fi
  sleep 4
done
echo "⚠️ HTTPS не піднявся. Перевір Hetzner Console → Firewalls (порти 80, 443) і: docker compose logs caddy"
