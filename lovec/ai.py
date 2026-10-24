"""AI-підсумок оголошення. Безкоштовно — Groq або Gemini (OpenAI-сумісний API); платно — Claude."""
from __future__ import annotations

import json
import logging
import re

import httpx

log = logging.getLogger(__name__)

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


PROVIDERS = {
    # безкоштовні тарифи без картки: Groq (console.groq.com), Gemini (aistudio.google.com)
    "groq": {"base": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b"},
    "gemini": {"base": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-3.8-flash"},
    "anthropic": {"base": "https://api.anthropic.com/v1", "model": "claude-haiku-5-5"},
}


def summarize(row: dict, provider: str, api_key: str, model: str = "", base_url: str = "",
              timeout: float = 90) -> dict:
    if not api_key:
        raise AIError("Нема ключа AI у .env (GROQ_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY)")
    preset = PROVIDERS.get(provider, PROVIDERS["groq"])
    model = model or preset["model"]
    base = (base_url or preset["base"]).rstrip("/")
    if provider == "anthropic":
        text, usage = _anthropic(row, api_key, model, base, timeout)
    else:
        text, usage = _openai_compat(row, api_key, model, base, timeout)
    out = parse_json(text)
    out["_model"] = model
    out["_usage"] = usage
    return out


def _err(r: httpx.Response, who: str) -> AIError:
    try:
        j = r.json()
        msg = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else j.get("error")
        msg = msg or r.text[:200]
    except ValueError:
        msg = r.text[:200]
    if r.status_code == 429:
        return AIError(f"{who}: ліміт безкоштовного тарифу, спробуй за хвилину ({msg})")
    return AIError(f"{who} {r.status_code}: {msg}")


def _anthropic(row, key, model, base, timeout):
    r = httpx.post(f"{base}/messages", timeout=timeout, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
    }, json={"model": model, "max_tokens": 1200, "system": SYSTEM,
             "messages": [{"role": "user", "content": build_prompt(row)}]})
    if r.status_code != 200:
        raise _err(r, "Claude API")
    data = r.json()
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"), data.get("usage", {})


def _openai_compat(row, key, model, base, timeout):
    body = {"model": model, "max_tokens": 3000, "temperature": 0.3,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": build_prompt(row)}],
            "response_format": {"type": "json_object"}}
    headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
    r = httpx.post(f"{base}/chat/completions", timeout=timeout, headers=headers, json=body)
    if r.status_code == 400 and "response_format" in r.text:
        body.pop("response_format")  # не всі моделі вміють JSON-режим — тоді просто просимо в промпті
        r = httpx.post(f"{base}/chat/completions", timeout=timeout, headers=headers, json=body)
    if r.status_code != 200:
        raise _err(r, "AI API")
    data = r.json()
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise AIError("AI API: порожня відповідь")
    return text, data.get("usage", {})


def one_liner(ai: dict) -> str:
    """Для Telegram-повідомлення."""
    v = {"owner": "схоже на власника", "agent": "схоже на ріелтора", "unclear": "неясно хто"}.get(
        (ai.get("seller") or {}).get("verdict", ""), "")
    return " · ".join(x for x in [ai.get("tldr", ""), v] if x)
