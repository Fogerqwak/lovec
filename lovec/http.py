"""HTTP-клієнт з ввічливими паузами і ретраями."""
from __future__ import annotations

import logging
import random
import time

import httpx

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class Fetcher:
    def __init__(self, delay: tuple[float, float] = (2.0, 5.0)):
        self.delay = delay
        self._last = 0.0
        self.client = httpx.Client(
            headers={"User-Agent": UA, "Accept-Language": "sk-SK,sk;q=0.9,en;q=0.7",
                     "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            follow_redirects=True,
            timeout=25,
        )

    def get(self, url: str, retries: int = 2) -> str | None:
        for attempt in range(retries + 1):
            wait = random.uniform(*self.delay) - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.client.get(url)
                self._last = time.monotonic()
                if r.status_code == 200:
                    return r.text
                if r.status_code in (403, 429):
                    log.warning("%s -> %s (rate limit/ban?), чекаю", url, r.status_code)
                    time.sleep(30 * (attempt + 1))
                    continue
                log.warning("%s -> HTTP %s", url, r.status_code)
                return None
            except httpx.HTTPError as e:
                self._last = time.monotonic()
                log.warning("%s -> %s", url, e)
                time.sleep(5)
        return None

    def close(self):
        self.client.close()
