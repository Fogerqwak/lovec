from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Listing:
    source: str
    ext_id: str
    url: str
    title: str = ""
    price_eur: float | None = None
    price_text: str = ""
    location: str = ""
    description: str = ""
    seller_name: str = ""
    phones: list[str] = field(default_factory=list)
    seller_listing_count: int | None = None
    # Явна позначка сайту: True = приватна особа, False = компанія, None = невідомо
    site_private_flag: bool | None = None
    page_markers: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    district: str | None = None
    okres: str | None = None
    prop_type: str | None = None  # byt | izba | dom
    rooms: float | None = None
    area_m2: float | None = None

    # Заповнює класифікатор
    score: int = 0
    category: str = ""  # private | suspicious | agency
    reasons: list[str] = field(default_factory=list)
    wants_no_rk: bool = False

    @property
    def match_keys(self) -> list[str]:
        """Телефони + прихований id продавця (Bazoš ховає номер, але id в сторінці є)."""
        k = list(self.phones)
        if self.extra.get("seller_key"):
            k.append(self.extra["seller_key"])
        return k

    @property
    def key(self) -> str:
        return f"{self.source}:{self.ext_id}"


STATUSES = {
    "new": "🆕 Новий",
    "called": "📞 Подзвонив",
    "no_answer": "🔕 Не відповів",
    "meeting": "📅 Зустріч",
    "contract": "✍️ Договір",
    "rejected": "❌ Відмова",
    "not_interesting": "🙈 Не цікаво",
    "agent": "🏢 Це ріелтор",
}
