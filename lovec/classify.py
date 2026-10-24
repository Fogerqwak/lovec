"""Приватна особа чи ріелтор?

Два рівні:
  1) ЖОРСТКІ правила — лише там, де ріелтор 100%: ім'я продавця з real/s.r.o./maklér,
     профіль агентства на сайті, «Realitná kancelária X ponúka», телефон з чорного списку.
     Тільки вони ховають оголошення (category = agency).
  2) М'які сигнали (текст, «bez provízie», повтор телефону…) — нічого не ховають,
     лише вирішують 🟢 чи 🟡 і пишуть причину, щоб ти бачив, що насторожило.
"""
from __future__ import annotations

import re
import unicodedata

from .models import Listing


def fold(text: str) -> str:
    """нижній регістр + без діакритики: 'Realitná Kancelária' -> 'realitna kancelaria'"""
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


# ---------- ЖОРСТКІ: ім'я продавця/бренд ----------
# "real" ловить Realadr, DirectReal, RealityPro, Real Estate…; виключаємо випадкові слова.
HARD_NAME = re.compile(
    r"s\.? ?r\.? ?o\b|\bspol\b|\ba\. ?s\.|\bk\. ?s\.|\bv\. ?o\. ?s\."
    r"|real(?!iz|n[ey] (cas|zivot))|makler|property|properties|estate|invest|\bgroup\b|consulting"
    r"|development|developer|agentur|agency|\brk\b",
    re.I,
)
NOT_NAME = re.compile(r"israel|rafael", re.I)

# Самоідентифікація в тексті: так пише лише агентство
HARD_TEXT = [
    (r"\b(realitn\w* kancelari\w*|rk|realitka)\b[^.!\n]{0,40}\b(ponuka|ponukame|vam ponuka|ponuka vam|ponuka na)\b",
     "«Realitná kancelária … ponúka»"),
    (r"(©|\(c\)|autorskym dielom)[^.\n]{0,80}realitn\w* kancelari", "© фото/тексту агентства"),
    (r"\bvratane\b[^.\n]{0,30}\bproviz\w*", "«cena vrátane provízie»"),
    (r"\bproviz\w* (pre |pre nasu )?(realitn\w* kancelari\w*|rk)\b(?![^.\n]{0,20}(netreba|nie je|neplat|ziadn|\b0\b))", "«provízia pre RK»"),
]

# ---------- М'ЯКІ ----------
# Звернення до ріелторів — найчастіше приватник. Але так пишуть і хитрі агенти,
# тому бонус дається лише якщо в тексті нема жодної ознаки агентства.
ADDRESSED_TO_RK = [
    r"\bnie (som|sme) (rk|realitk\w*|realitna kancelaria|makler\w*)\b",
    r"\b(rk|realitk\w*|realitn\w* kancelari\w*|makler\w*)\b[^.!\n]{0,40}\b(nevol\w*|nekontakt\w*|neotravuj\w*|nepiste|nepisat|prosim nie|vyhrad\w*|vylucne)",
    r"\b(nevolajte|nepiste|nekontaktujte)\b[^.!\n]{0,20}\b(rk|realitk\w*|realitn\w*|makler\w*)",
    r"\bbez (rk|realitk\w*|realitnej kancelarie|sprostredkovatel\w*|maklera)\b(?! provizi)",
]
# «Без провізії» — нейтрально: агентства так пишуть для оренди (платить власник).
NO_COMMISSION = [
    r"\bbez (rk |realitnej |realitk\w* )?provizi\w*",
    r"\b(netreba|nie je potrebne|nemusite|neplatite)\w* (platit\w* )?proviz\w*[^.!\n]{0,30}",
    r"\b0 ?% provizi\w*",
]

PRIVATE = [
    (r"\b(priamo )?od majitel\w*\b", "«od majiteľa»"),
    (r"\b(som|sme) majitel\w*\b", "«som majiteľ»"),
    (r"\bsukromn\w* (osoba|inzerent|predaj|prenajom)\b", "«súkromná osoba»"),
    (r"\b(predam|prenajmem|prenajmeme|predame|prenajimam)\b", "1-ша особа: predám/prenajmem"),
    (r"\b(nas|moj|svoj|nasu|moju) (byt|dom|garsonku|garsonka|rodinny dom)\b", "«náš/môj byt»"),
    (r"\b(stahujeme sa|stahovanie|odchod do zahranicia|dedicstvo|zdedeny)\b", "життєва причина"),
]

SOFT_AGENCY = [
    (r"\brealitn\w* kancelari\w*\b", 2, "згадка «realitná kancelária»"),
    (r"\b(makler\w*|realitn\w* (agent|specialist|poradc\w*))\b", 2, "маклер/агент у тексті"),
    (r"\bproviz\w*\b", 2, "згадка провізії"),
    (r"\b(rezervacn\w* (poplatok|zmluv\w*|zaloh\w*)|zmluv\w* o sprostredkovani)", 2, "резерваційний договір"),
    (r"\b(vratane|vrátane) (kompletn\w* )?(realitn\w*|pravn\w*|odborn\w*) servis\w*", 2, "«vrátane realitného servisu»"),
    (r"\bexkluziv\w*\b", 1, "«exkluzívne»"),
    (r"\bv (exkluzivnom )?zastupeni\b", 2, "«v zastúpení»"),
    (r"(©|\(c\)) ?(text|fotografie)|autorskym dielom", 2, "© тексту/фото"),
    (r"\bs\.? ?r\.? ?o\.?(?=\W|$)|\bspol\. s r\. ?o\.", 1, "s.r.o. в тексті"),
    (r"\bhypotek\w*[^.\n]{0,30}(zabezpec|vybav|pomoz|sprostredk)", 1, "пропонують іпотеку"),
    (r"\b(3d (prehliadk|obhliadk)\w*|matterport|virtualn\w* prehliadk\w*)", 1, "3D-тур"),
    (r"\b(radi vas|nevahajte nas) (prevedieme|kontaktovat)", 1, "«radi Vás prevedieme»"),
    (r"\bponukame (vam )?(na predaj|na prenajom)\b", 1, "«ponúkame Vám»"),
]

TITLE_BRAND = re.compile(r"^\s*([A-ZÁÄČĎÉÍĽĹŇÓÔŔŠŤÚÝŽ0-9&.\s]{3,30}?)\s*[-–|︱:]\s*")


# «Шукаю житло» (попит), а не пропозиція
WHAT = r"(byt\w*|garsonk\w*|garzonk\w*|izb\w*|dom\w*|podnajom\w*|prenajom\w*|pozemok|bydlen\w*|byvanie|\d[,.]?5? ?-? ?izb\w*|\d ?i\b)"
SEEKER_TITLE = [
    r"^\W*(hladam|hladame|kupim|kupime|zhanam|zhaname|potrebujem|potrebujeme|prenajmem si|prenajmeme si)\b",
    rf"\b(hladam|hladame|hlada sa|hladaju|kupim|kupime|zhanam|zhaname)\b[^.!]{{0,40}}\b{WHAT}",
    r"\bdopyt\b",
    r"\b(student\w*|par|rodina|manzelia|pracujuci\w*|slusn\w*)\b[^.!]{0,25}\bhlada\w*",
]
SEEKER_DESC = rf"^\W*(\w+\W+){{0,6}}(hladam|hladame|zhanam|zhaname|kupim|kupime)\b[^.!]{{0,60}}\b{WHAT}"
OFFER = r"\b(prenajmem|predam|ponukam|ponukame|predame|prenajmeme)\b(?! si)"


def is_seeker(title: str, description: str = "") -> bool:
    t, d = fold(title), fold(description)[:400]
    if any(re.search(p, t) for p in SEEKER_TITLE):
        return not re.search(OFFER, t)
    # заголовок нейтральний («2i byt Ružinov»), але текст починається з «Hľadám…»
    return bool(re.search(SEEKER_DESC, d)) and not re.search(OFFER, t + " " + d[:150])


def is_company_name(name: str) -> bool:
    n = fold(name)
    return bool(n and HARD_NAME.search(n) and not NOT_NAME.search(n))


def classify(l: Listing, phone_reuse: int = 0, blacklisted: bool = False, *,
             phone_reuse_limit: int = 3, private_max: int = 1, **_ignored) -> Listing:
    score = 0
    reasons: list[str] = []
    hard: list[str] = []

    def add(pts: int, why: str):
        nonlocal score
        score += pts
        reasons.append(f"{'+' if pts > 0 else ''}{pts} {why}")

    text = fold(f"{l.title}. {l.description}")

    # ===== 1. ЖОРСТКІ (100%) =====
    if blacklisted:
        hard.append("телефон у чорному списку (ти позначив як ріелтора)")
    if l.site_private_flag is False:
        hard.append("сайт: профіль агентства/маклера")
    if l.seller_name and is_company_name(l.seller_name):
        hard.append(f"продавець: «{l.seller_name[:40]}»")
    m = TITLE_BRAND.match(l.title or "")
    if m and is_company_name(m.group(1)):
        hard.append(f"бренд у заголовку: «{m.group(1).strip()}»")

    # «без провізії» вирізаємо до всіх перевірок, щоб слово «provízia» в ньому нічого не важило
    no_commission = False
    for pat in NO_COMMISSION:
        if re.search(pat, text):
            no_commission = True
            text = re.sub(pat, " ", text)

    addressed = False
    for pat in ADDRESSED_TO_RK:
        if re.search(pat, text):
            addressed = True
            text = re.sub(pat, " ", text)

    for pat, why in HARD_TEXT:
        if re.search(pat, text):
            hard.append(why)

    # ===== 2. М'ЯКІ =====
    soft_agency = 0
    for pat, pts, why in SOFT_AGENCY:
        if re.search(pat, text):
            soft_agency += pts
            reasons.append(f"+{pts} {why}")
    score += min(soft_agency, 6)

    if addressed:
        l.wants_no_rk = True
        if soft_agency == 0:
            add(-2, "просить без ріелторів")
        else:
            reasons.append("0 «без RK», але є ознаки агентства — не віримо")
    if no_commission:
        reasons.append("0 «bez provízie» — так пишуть і агентства")

    priv = 0
    for pat, why in PRIVATE:
        if re.search(pat, text):
            priv += 1
            reasons.append(f"-1 {why}")
    score -= min(priv, 3)

    if l.site_private_flag is True:
        add(-1, "сайт: приватна особа (галочку ставлять і ріелтори)")
    if "rk_box" in l.page_markers:
        add(3, "блок «Realitná kancelária» у контактах")
    if m and not hard and not re.match(r"^(predam|predaj|prenajom|prenajmem|na predaj|na prenajom)\b", fold(l.title)):
        add(1, "назва великими літерами перед заголовком")

    if l.seller_listing_count is not None:
        if l.seller_listing_count >= 6:
            add(3, f"у продавця {l.seller_listing_count} оголошень")
        elif l.seller_listing_count >= 3:
            add(1, f"у продавця {l.seller_listing_count} оголошення")

    if phone_reuse + 1 >= phone_reuse_limit:
        add(3, f"той самий продавець у {phone_reuse + 1} оголошеннях")
    elif phone_reuse >= 1:
        add(1, "продавець ще в 1 оголошенні")

    l.score = score
    if is_seeker(l.title, l.description):
        l.category = "seeker"
        l.reasons = ["🔍 шукає житло, а не пропонує"] + [f"🔴 {h}" for h in hard] + reasons
        return l
    if hard:
        l.category = "agency"
        l.reasons = [f"🔴 {h}" for h in hard] + reasons
    else:
        l.category = "private" if score <= private_max else "suspicious"
        l.reasons = reasons
    return l
