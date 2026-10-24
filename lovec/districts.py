"""Район Братислави з адреси/тексту/PSČ.

Порядок: явна адреса «Bratislava-Petržalka» → назва району чи мікрорайону в заголовку/локації →
поштовий індекс → назва в описі. Нічого не знайшли — None («невідомо»).
Не Братислава (Senec, Pezinok, Dunajská Lužná…) — "Mimo BA".
"""
from __future__ import annotations

import re

from .classify import fold

DISTRICTS = [
    "Staré Mesto", "Ružinov", "Nové Mesto", "Petržalka", "Karlova Ves", "Dúbravka", "Rača",
    "Vrakuňa", "Podunajské Biskupice", "Lamač", "Devínska Nová Ves", "Devín", "Záhorská Bystrica",
    "Vajnory", "Jarovce", "Rusovce", "Čunovo",
]
OUTSIDE = "Mimo BA"

# Мікрорайони й відомі місця → район (у fold-формі, без діакритики)
ALIASES = {
    "Staré Mesto": ["stare mesto", "stareho mesta", "starom meste", "eurovea", "palisady",
                    "hviezdoslavov", "panska", "obchodna ulica", "kapucinska", "sulekova", "vajanskeho nabr"],
    "Ružinov": ["ruzinov", "nivy", "mlynske nivy", "trnavka", "prievoz", "strkovec", "pazitkova", "ostredky",
                "mierova kolonia", "trnavske myto", "bajkalsk", "ruzinovsk", "klincova"],
    "Nové Mesto": ["nove mesto", "kramare", "koliba", "jurajov dvor", "zahradky", "mytna", "racianske myto",
                   "racianska", "kuchajda", "vinohrady", "pasienky", "jelsova", "slovany"],
    "Petržalka": ["petrzalk", "slnecnic", "ovsiste", "kopcany", "dolnozemsk",
                  "panonska", "einpark", "aupark", "jungmannova", "romanova"],
    "Karlova Ves": ["karlova ves", "karlovej vsi", "dlhe diely", "kresankova", "silvanova", "kuklovska"],
    "Dúbravka": ["dubravk", "agatova", "ozvoldikova", "saratovsk"],
    "Rača": ["rac[aiue]\\b", "krasnany", "vychodne", "pekna cesta", "barania"],
    "Vrakuňa": ["vrakun"],
    "Podunajské Biskupice": ["podunajsk", "biskupic", "kazansk", "medzi jarkami"],
    "Lamač": ["lamac", "bory"],
    "Devínska Nová Ves": ["devinska nova ves", "devinskej novej vsi", "dnv"],
    "Devín": ["(?<!hrad )devin\\b(?!sk)"],
    "Záhorská Bystrica": ["zahorsk\\w* bystric"],
    "Vajnory": ["vajnor"],
    "Jarovce": ["jarovc"],
    "Rusovce": ["rusovc"],
    "Čunovo": ["cunov"],
}
# «Bratislava-Petržalka», «BA - Ružinov», «Bratislava V»
EXPLICIT = re.compile(r"(?:bratislav\w*|\bba)\s*[-–]\s*([a-z ]{3,30})")
OKRES = {"bratislava i": "Staré Mesto"}  # інші округи містять кілька районів — не вгадуємо

# PSČ: беремо лише те, в чому впевнені
PSC5 = {
    "82106": "Podunajské Biskupice", "82107": "Vrakuňa",
    "83106": "Rača", "83107": "Vajnory",
    "84101": "Dúbravka", "84102": "Dúbravka", "84103": "Lamač", "84104": "Karlova Ves", "84105": "Karlova Ves",
    "84106": "Záhorská Bystrica", "84107": "Devínska Nová Ves", "84110": "Devín",
}
PSC3 = {"811": "Staré Mesto", "851": "Petržalka", "821": "Ružinov", "831": "Nové Mesto"}
PSC_RE = re.compile(r"\b(8[1-5]\d)\s?(\d{2})\b")
OUTSIDE_TOWNS = ["senec", "pezinok", "dunajska luzna", "ivanka pri dunaji", "chorvatsky grob", "bernolakovo",
                 "stupava", "marianka", "zahorska ves", "malacky", "samorin", "rovinka", "miloslavov",
                 "most pri bratislave", "svaty jur", "modra", "slovensky grob", "limbach", "kosice", "nitra",
                 "trnava", "zilina", "presov", "banska bystrica", "poprad", "topolcany", "hamuliakovo",
                 "kalinkovo", "tomasov"]

_ALIAS_RE = {d: re.compile(r"\b(?:" + "|".join(a) + r")") for d, a in ALIASES.items()}


def _by_name(text: str) -> str | None:
    # беремо найранішу згадку: «Byt v Ružinove, blízko Petržalky» → Ružinov
    best = None
    for d, rx in _ALIAS_RE.items():
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), d)
    return best[1] if best else None


def detect(location: str = "", title: str = "", description: str = "") -> str | None:
    loc, tit, desc = fold(location), fold(title), fold(description)[:1500]

    for src in (loc, tit):
        m = EXPLICIT.search(src)
        if m:
            d = _by_name(m.group(1) + " ")
            if d:
                return d
    for k, v in OKRES.items():
        if re.search(rf"okres {k}\b(?!i|v)", loc):
            return v

    m = PSC_RE.search(location or "")
    if m and "bratislav" in loc:
        d = PSC5.get(m.group(1) + m.group(2))
        if d:
            return d

    d = _by_name(loc + " ") or _by_name(tit + " ")
    if d:
        return d

    if m and "bratislav" in loc and PSC3.get(m.group(1)):
        return PSC3[m.group(1)]

    head = f"{loc} {tit}"
    if "bratislav" not in head and any(re.search(rf"\b{t}", head) for t in OUTSIDE_TOWNS):
        return OUTSIDE

    d = _by_name(desc + " ")
    if d:
        return d
    if any(re.search(rf"\b{t}", head + " " + desc[:300]) for t in OUTSIDE_TOWNS) and "bratislav" not in head:
        return OUTSIDE
    return None
