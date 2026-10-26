"""Загальний парсер. Сайти міняють верстку, тому ставка на стабільні речі:
посилання за шаблоном URL, og:-мета, JSON-LD, текст сторінки. Специфічні
селектори кожного сайту — лише як перша спроба, з фолбеком на загальне."""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Listing

PHONE_RE = re.compile(r"(?<!\d)(?:\+\s?421|00421|0)\s?(9\d{2})[\s\-/.]?(\d{3})[\s\-/.]?(\d{3})(?!\d)")
PRICE_RE = re.compile(r"(\d{1,3}(?:[  .]\d{3})+|\d{3,7})(?:[,.]\d{1,2})?\s?(?:€|eur\b|EUR\b)")


def normalize_phones(text: str) -> list[str]:
    out = []
    for m in PHONE_RE.finditer(text or ""):
        p = "421" + "".join(m.groups())
        if p not in out:
            out.append(p)
    return out


def parse_price(text: str) -> tuple[float | None, str]:
    m = PRICE_RE.search(text or "")
    if not m:
        return None, ""
    num = re.sub(r"[  .]", "", m.group(1))
    try:
        return float(num), m.group(0).strip()
    except ValueError:
        return None, m.group(0).strip()


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


class Source:
    name = "base"
    # regex на шлях детальної сторінки; група "id" — ідентифікатор оголошення
    detail_re: re.Pattern = re.compile(r"$^")
    description_selectors: list[str] = []
    title_selectors: list[str] = ["h1"]

    # ---------- список ----------
    def extract_links(self, html: str, base_url: str) -> list[tuple[str, str]]:
        """-> [(ext_id, absolute_url)] без дублів, у порядку появи."""
        soup = BeautifulSoup(html, "lxml")
        seen: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            url = urljoin(base_url, a["href"]).split("#")[0]
            m = self.detail_re.search(url)
            if m and m.group("id") not in seen:
                seen[m.group("id")] = self.canonical(url, m)
        return list(seen.items())

    def canonical(self, url: str, m: re.Match) -> str:
        return url.split("?")[0]

    NEXT_TEXT = re.compile(r"^\s*(ďalšia|ďalšie|ďalej|nasledujúca|next|›|»|>)\s*$", re.I)

    def next_page(self, html: str, url: str, page_no: int) -> str | None:
        """URL наступної сторінки списку (для догрузки старих). page_no — номер поточної (1, 2, …)."""
        soup = BeautifulSoup(html, "lxml")
        tag = soup.find("link", rel="next") or soup.find("a", rel="next")
        if tag and tag.get("href"):
            return urljoin(url, tag["href"])
        for a in soup.find_all("a", href=True):
            txt = clean(a.get_text(" "))
            if self.NEXT_TEXT.match(txt) or (a.get("aria-label") or "").lower() in ("next", "ďalšia", "nasledujúca strana"):
                return urljoin(url, a["href"])
        return self.page_url(url, page_no + 1)

    def page_url(self, url: str, page: int) -> str | None:
        """Фолбек: ?page=N."""
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        p = urlsplit(url)
        q = dict(parse_qsl(p.query))
        q["page"] = str(page)
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))

    def list_dates(self, html: str) -> list:
        """Дати оголошень на сторінці списку, якщо сайт їх показує (для зупинки за N днів)."""
        return []

    # ---------- деталка ----------
    def parse_detail(self, html: str, ext_id: str, url: str) -> Listing:
        soup = BeautifulSoup(html, "lxml")
        l = Listing(source=self.name, ext_id=ext_id, url=url)
        ld = self._jsonld(soup)

        l.title = clean(self._first_text(soup, self.title_selectors)
                        or self._meta(soup, "og:title") or ld.get("name") or (soup.title.string if soup.title else ""))
        l.description = self._description(soup, ld)

        price = ld.get("price")
        if price:
            try:
                l.price_eur = float(str(price).replace(" ", "").replace(",", "."))
                l.price_text = f"{l.price_eur:,.0f} €".replace(",", " ")
            except ValueError:
                pass
        if l.price_eur is None:
            l.price_eur, l.price_text = parse_price(self._price_zone(soup) or l.title + " " + l.description[:600])

        loc = clean(ld.get("locality") or "")
        l.location = "" if loc.lower() in ("slovensko", "slovakia", "sk") else loc  # країна — не локація
        l.seller_name = clean(ld.get("seller") or "")

        page_text = clean(soup.get_text(" "))
        if l.price_eur is None and l.title:
            i = page_text.find(l.title[:40])
            if i >= 0:
                l.price_eur, l.price_text = parse_price(page_text[i:i + 1500])
        l.phones = normalize_phones(l.description + " " + self._contact_zone(soup))
        l.page_markers = self._markers(page_text)
        try:
            from ..photos import extract_image_urls
            l.images = extract_image_urls(html, url, self.name, ext_id)
        except Exception:  # noqa: BLE001 — фото не мають ламати парсинг оголошення
            l.images = []
        self.enrich(soup, l, page_text)
        return l

    # Хуки для конкретних сайтів
    def enrich(self, soup: BeautifulSoup, l: Listing, page_text: str) -> None:
        pass

    def _price_zone(self, soup: BeautifulSoup) -> str:
        return ""

    def _contact_zone(self, soup: BeautifulSoup) -> str:
        """Текст блоку контактів. За замовчуванням — посилання tel:."""
        tels = [a["href"][4:] for a in soup.select('a[href^="tel:"]')
                if not a.find_parent(["header", "footer", "nav"])]  # телефони самого сайту не беремо
        return " ".join(tels)

    # ---------- утиліти ----------
    @staticmethod
    def _markers(page_text: str) -> list[str]:
        """Однозначні позначки сайту на всій сторінці (не в описі)."""
        from ..classify import fold
        t = fold(page_text)
        out = []
        if re.search(r"\bprofi klient\b", t):
            out.append("profi_klient")
        if re.search(r"\bsukromn(a|y) (osoba|inzerent)\b", t):
            out.append("sukromna_osoba")
        return out

    @staticmethod
    def _meta(soup: BeautifulSoup, prop: str) -> str:
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return tag.get("content", "") if tag else ""

    @staticmethod
    def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
        for sel in selectors:
            el = soup.select_one(sel)
            if el and clean(el.get_text(" ")):
                return clean(el.get_text(" "))
        return ""

    def _description(self, soup: BeautifulSoup, ld: dict) -> str:
        cands = []
        for sel in self.description_selectors:
            el = soup.select_one(sel)
            if el:
                cands.append(clean(el.get_text(" ")))
                break
        if not cands or len(cands[0]) < 80:
            cands += [clean(ld.get("description")), clean(self._meta(soup, "og:description"))]
            cands.append(self._largest_block(soup))
        cands = [c for c in cands if c]
        return max(cands, key=len) if cands else ""

    @staticmethod
    def _largest_block(soup: BeautifulSoup) -> str:
        soup = BeautifulSoup(str(soup), "lxml")  # копія: оригінал (з JSON у <script>) ще знадобиться в enrich
        for t in soup(["script", "style", "noscript", "nav", "header", "footer"]):
            t.decompose()
        best = ""
        for el in soup.find_all(["p", "div", "section", "article", "td"]):
            if el.find(["div", "section", "article", "table", "ul"]):
                continue  # беремо лише "листові" блоки, інакше виграє <body>
            txt = clean(el.get_text(" "))
            if len(txt) > len(best):
                best = txt
        return best

    @staticmethod
    def _jsonld(soup: BeautifulSoup) -> dict:
        out: dict = {}

        def walk(o):
            if isinstance(o, list):
                for x in o:
                    walk(x)
            elif isinstance(o, dict):
                if "@graph" in o:
                    walk(o["@graph"])
                offers = o.get("offers")
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                if isinstance(offers, dict):
                    out.setdefault("price", offers.get("price"))
                    seller = offers.get("seller") or offers.get("offeredBy")
                    if isinstance(seller, dict):
                        out.setdefault("seller", seller.get("name"))
                if o.get("@type") in ("Product", "Offer", "RealEstateListing", "Residence", "Apartment",
                                      "House", "SingleFamilyResidence", "Accommodation"):
                    out.setdefault("name", o.get("name"))
                    out.setdefault("description", o.get("description"))
                addr = o.get("address")
                if isinstance(addr, dict):
                    out.setdefault("locality", addr.get("addressLocality"))
                for v in o.values():
                    if isinstance(v, (dict, list)):
                        walk(v)

        for s in soup.find_all("script", type="application/ld+json"):
            try:
                walk(json.loads(s.string or ""))
            except (json.JSONDecodeError, TypeError):
                continue
        return {k: v for k, v in out.items() if v}
