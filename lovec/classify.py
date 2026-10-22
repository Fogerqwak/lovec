"""Приватна особа чи ріелтор? Простий прозорий скоринг.

score > 0 тягне в бік агентства, < 0 — у бік приватної особи.
Кожне правило пише причину в reasons, щоб у Telegram/дашборді було видно ЧОМУ.
Ріелтори часто ставлять галочку "приватна особа", тому одній галочці не віримо.
"""
from __future__ import annotations

import re
import unicodedata

from .models import Listing


def fold(text: str) -> str:
    """нижній регістр + без діакритики: 'Realitná Kancelária' -> 'realitna kancelaria'"""
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


# Фрази "ріелторам не дзвонити" — це ПРИВАТНИЙ продавець (але дзвонити треба делікатно)
NO_RK = [
    r"\bbez (rk|realit\w*|provizi\w*|sprostredkovatel\w*)\b",
    r"\bnie (som )?(rk|realitk\w*|realitna kancelaria)\b",
    r"\b(rk|realitk\w*|realitn\w* kancelari\w*|makler\w*)\b[^.!\n]{0,40}\b(nevol\w*|nekontakt\w*|neotravuj\w*|neprosim|nepiste|nepisat|prosim nie|vyhrad\w*|vylucne)",
    r"\b(nevolajte|nepiste|nekontaktujte)\b[^.!\n]{0,20}\b(rk|realitk\w*|realitn\w*)",
    r"\bnetreba platit proviziu\b",
    r"\bnie je potrebne platit\w* proviziu\b",
]

PRIVATE = [
    (r"\b(priamo )?od majitel\w*\b", "«od majiteľa»"),
    (r"\bsukromn\w* (osoba|inzerent|predaj)\b", "«súkromná osoba» в тексті"),
    (r"\b(predam|prenajmem|prenajmeme|predame)\b", "1-ша особа: predám/prenajmem"),
    (r"\b(ponukam|ponukame) (na )?(predaj|prenajom)\b(?![^.]{0,40}(kancelar|realit|s\.r\.o))", "«ponúkam na predaj» без фірми"),
    (r"\b(nas|moj|svoj) (byt|dom|rodinny dom)\b", "«náš/môj byt»"),
    (r"\b(stahujeme sa|stahovanie|odchod do zahranicia|dedicstvo|zdedeny)\b", "життєва причина продажу"),
]

AGENCY = [
    (r"\brealitn\w* kancelari\w*\b(?! (nevol|nekontakt|prosim nie))", 3, "«realitná kancelária»"),
    (r"\b(rk|realitka) (ponuka|ponukame|vam ponuka)\b", 3, "«RK ponúka»"),
    (r"\b(makler\w*|realitn\w* (makler|agent|specialist|maklerka|poradc\w*))\b", 2, "маклер/агент"),
    (r"\bproviz\w*\b", 2, "згадка провізії"),
    (r"\b(vrátane|vratane) (kompletn\w* )?(realitn\w*|pravn\w*|odborn\w*) servis\w*", 3, "«vrátane realitného servisu»"),
    (r"\bexkluziv\w*\b", 1, "«exkluzívne»"),
    (r"\bv (exkluzivnom )?zastupeni\b", 3, "«v zastúpení»"),
    (r"(©|\(c\)) ?(text|fotografie)|autorskym dielom", 3, "© тексту/фото"),
    (r"\bs\.? ?r\.? ?o\.?(?=\W|$)|\bspol\. s r\. ?o\.", 2, "s.r.o. в тексті"),
    (r"\b(hypotekarn\w* (poradenstvo|servis|uver\w*) (zabezpec|vybav|pomoz))", 2, "пропонують іпотеку"),
    (r"\b(3d (prehliadk|obhliadk)\w*|matterport|virtualn\w* prehliadk\w*)", 1, "3D-тур"),
    (r"\bvizualizaci\w*\b", 1, "візуалізації"),
    (r"\b(radi vas|nevahajte nas) (prevedieme|kontaktovat)", 1, "«radi Vás prevedieme»"),
    (r"\bponukame (vam )?(na predaj|na prenajom)\b[^.]{0,40}(kancelar|realit|s\.r\.o|group|reality)", 3, "фірма «ponúkame»"),
]

SELLER_AGENCY = re.compile(r"s\.? ?r\.? ?o|a\.s\.|reality|realit|real\b|group|property|invest|home|estate|makler", re.I)
TITLE_BRAND = re.compile(r"^[A-ZÁÄČĎÉÍĽĹŇÓÔŔŠŤÚÝŽ0-9&.\s]{3,30}\s[-–|:]\s")


def classify(l: Listing, phone_reuse: int = 0, blacklisted: bool = False, *,
             phone_reuse_limit: int = 3, private_max: int = 1, suspicious_max: int = 3) -> Listing:
    score = 0
    reasons: list[str] = []

    def add(pts: int, why: str):
        nonlocal score
        score += pts
        reasons.append(f"{'+' if pts > 0 else ''}{pts} {why}")

    text = fold(f"{l.title}. {l.description}")

    # 1) "без ріелторів" — вирізаємо ці фрази, щоб слово "RK" у них не рахувалось як агентство
    for pat in NO_RK:
        if re.search(pat, text):
            l.wants_no_rk = True
            text = re.sub(pat, " ", text)
    if l.wants_no_rk:
        add(-3, "просить без RK")

    # 2) явна позначка сайту
    if l.site_private_flag is True:
        add(-2, "сайт: приватна особа")
    elif l.site_private_flag is False:
        add(5, "сайт: компанія/Profi")
    if "rk_box" in l.page_markers:
        add(3, "блок «Realitná kancelária» у контактах")

    # 3) текст
    agency_pts = 0
    for pat, pts, why in AGENCY:
        if re.search(pat, text):
            agency_pts += pts
            reasons.append(f"+{pts} {why}")
    score += min(agency_pts, 8)

    private_pts = 0
    for pat, why in PRIVATE:
        if re.search(pat, text):
            private_pts += 1
            reasons.append(f"-1 {why}")
    score -= min(private_pts, 3)

    if TITLE_BRAND.match(l.title or "") and not re.match(r"^(predam|predaj|prenajom|prenajmem|na predaj)\b", fold(l.title)):
        add(1, "бренд великими літерами в заголовку")

    # 4) продавець
    if l.seller_name and SELLER_AGENCY.search(fold(l.seller_name)):
        add(3, f"продавець: {l.seller_name[:30]}")
    if l.seller_listing_count is not None:
        if l.seller_listing_count >= 6:
            add(4, f"у продавця {l.seller_listing_count} оголошень")
        elif l.seller_listing_count >= 3:
            add(1, f"у продавця {l.seller_listing_count} оголошення")

    # 5) телефон
    if blacklisted:
        add(6, "телефон у чорному списку (позначений як ріелтор)")
    elif phone_reuse + 1 >= phone_reuse_limit:
        add(5, f"телефон у {phone_reuse + 1} оголошеннях")
    elif phone_reuse >= 1:
        add(1, "телефон ще в 1 оголошенні")

    l.score = score
    l.reasons = reasons
    if score <= private_max:
        l.category = "private"
    elif score <= suspicious_max:
        l.category = "suspicious"
    else:
        l.category = "agency"
    return l
