"""AI-підсумок оголошення через Claude API (Messages API, без SDK — лише httpx)."""
from __future__ import annotations

import json
import logging
import re

import httpx

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM = """Ти — асистент ріелтора-початківця в Братиславі. Він шукає оголошення від власників
(без агентства), дзвонить їм і пропонує свої послуги посередника: знайти орендаря/покупця.
Тобі дають оголошення словацькою. Відповідай УКРАЇНСЬКОЮ (крім поля opener_sk — словацькою).
Будь конкретним і коротким. Нічого не вигадуй: якщо факту нема в тексті — null.
Поверни ЛИШЕ JSON без пояснень і без ```."""

SCHEMA = """{
  "tldr": "одне речення: що це і для кого",
  "facts": {
    "type": "напр. 2-кімнатна квартира / будинок / гарсоньєра",
    "area_m2": "число або null",
    "price": "ціна як у тексті, з /міс для оренди",
    "extra_costs": "енергії/поплатки або null",
    "deposit": "застава або null",
    "available_from": "з якої дати або null",
    "furnished": "так / ні / частково / null",
    "pets": "так / ні / null",
    "parking": "коротко або null",
    "location": "вулиця/район"
  },
  "seller": {"verdict": "owner | agent | unclear", "why": "1-2 речення: на чому базується висновок"},
  "pluses": ["до 3 сильних сторін для орендаря/покупця"],
  "risks": ["до 3 ризиків/дивних моментів: завищена ціна, нема фото, неясні платежі…"],
  "pitch": "2-3 речення: як ріелтору зайти до цього власника, яку цінність запропонувати. Якщо власник просить без RK — врахуй це і запропонуй делікатний підхід або чесно скажи, що краще не дзвонити",
  "opener_sk": "перша фраза дзвінка словацькою, ввічливо, чесно представитися як realitný maklér, до 2 речень",
  "questions": ["до 4 питань власнику, яких бракує в оголошенні"]
}"""


class AIError(Exception):
    pass


def build_prompt(row: dict) -> str:
    deal = "оренда" if row.get("deal") == "prenajom" else "продаж"
    reasons = row.get("reasons")
    if isinstance(reasons, str):
        try:
            reasons = json.loads(reasons)
        except ValueError:
            reasons = []
    return (
        f"Тип угоди: {deal}\nДжерело: {row.get('source')}\n"
        f"Заголовок: {row.get('title') or ''}\n"
        f"Ціна (парсер): {row.get('price_text') or '—'}\nЛокація (парсер): {row.get('location') or '—'}\n"
        f"Продавець (ім'я на сайті): {row.get('seller_name') or '—'}\n"
        f"Сигнали автоматичного фільтра: {'; '.join(reasons or []) or '—'}\n\n"
        f"Текст оголошення:\n{(row.get('description') or '')[:6000]}\n\n"
        f"Поверни JSON такої структури:\n{SCHEMA}"
    )


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AIError("модель не повернула JSON")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise AIError(f"кривий JSON: {e}") from e


def summarize(row: dict, api_key: str, model: str, timeout: float = 60) -> dict:
    if not api_key:
        raise AIError("Нема ANTHROPIC_API_KEY у .env")
    r = httpx.post(API_URL, timeout=timeout, headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json",
    }, json={
        "model": model, "max_tokens": 1200, "system": SYSTEM,
        "messages": [{"role": "user", "content": build_prompt(row)}],
    })
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", r.text[:200])
        except ValueError:
            msg = r.text[:200]
        raise AIError(f"Claude API {r.status_code}: {msg}")
    data = r.json()
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    out = parse_json(text)
    out["_model"] = model
    out["_usage"] = data.get("usage", {})
    return out


def one_liner(ai: dict) -> str:
    """Для Telegram-повідомлення."""
    v = {"owner": "схоже на власника", "agent": "схоже на ріелтора", "unclear": "неясно хто"}.get(
        (ai.get("seller") or {}).get("verdict", ""), "")
    return " · ".join(x for x in [ai.get("tldr", ""), v] if x)
