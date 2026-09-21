"""Public Mainnet observations. No credentials or exchange write endpoint."""
import hashlib
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
import requests
from .config import dec


class Market:
    def __init__(self, session=None, clock=time.time):
        self.session = session or requests.Session()
        self.clock = clock

    def info(self, body):
        response = self.session.post("https://api.hyperliquid.xyz/info", json=body, timeout=(5, 10))
        response.raise_for_status()
        return response.json()

    def snapshot(self):
        book = self.info({"type": "l2Book", "coin": "BTC"})
        meta, contexts = self.info({"type": "metaAndAssetCtxs"})
        index = next(i for i, a in enumerate(meta["universe"]) if a["name"] == "BTC")
        bids, asks = book["levels"]
        mark = dec(contexts[index]["markPx"])
        if not bids or not asks or mark <= 0 or dec(bids[0]["px"]) >= dec(asks[0]["px"]):
            raise ValueError("invalid or crossed book")
        return {"mark": str(mark), "bids": bids, "asks": asks,
                "funding_rate": contexts[index]["funding"], "at": book["time"] / 1000,
                "received_at": self.clock(), "step": str(dec(10) ** -int(meta["universe"][index]["szDecimals"]))}

    def candles(self, interval, start, end):
        seconds = {"1m": 60, "5m": 300, "1h": 3600, "4h": 14400}[interval]
        result = self.info({"type": "candleSnapshot", "req": {"coin": "BTC", "interval": interval,
                           "startTime": int(start * 1000), "endTime": int(end * 1000)}})
        rows = sorted((b for b in result if int(b["t"]) / 1000 + seconds <= end), key=lambda b: b["t"])
        timestamps = [int(b["t"]) for b in rows]
        if len(set(timestamps)) != len(timestamps):
            raise ValueError("duplicate candles")
        for b in rows:
            o, h, low, c = (dec(b[k]) for k in ("o", "h", "l", "c"))
            if min(o, h, low, c) <= 0 or low > min(o, c) or h < max(o, c) or dec(b["v"]) < 0:
                raise ValueError("invalid candle")
        return rows

    def history(self, since, until):
        bars = self.candles("1m", since // 60 * 60, until)
        funding, cursor = [], int(since * 1000)
        for _ in range(20):
            page = self.info({"type": "fundingHistory", "coin": "BTC", "startTime": cursor, "endTime": int(until * 1000)})
            funding.extend(page)
            if len(page) < 500:
                break
            next_cursor = max(int(f["time"]) for f in page) + 1
            if next_cursor <= cursor:
                raise ValueError("funding pagination did not advance")
            cursor = next_cursor
        else:
            raise ValueError("funding pagination limit")
        expected = set(range(int(since // 3600 + 1) * 3600, int(until // 3600) * 3600 + 1, 3600))
        found = {int(f["time"]) // 1000 for f in funding}
        if expected - found:
            raise ValueError("funding history gap")
        return bars, funding

    def features(self, now):
        output = {}
        for interval, seconds, count in (("5m", 300, 60), ("1h", 3600, 60), ("4h", 14400, 60)):
            end = int(now // seconds) * seconds
            bars = self.candles(interval, end - count * seconds, end)
            expected = [int((end - (count - i) * seconds) * 1000) for i in range(count)]
            if [int(b["t"]) for b in bars] != expected:
                raise ValueError(f"incomplete {interval} history")
            closes = [dec(b["c"]) for b in bars]
            tr = [max(dec(bars[i]["h"]) - dec(bars[i]["l"]), abs(dec(bars[i]["h"]) - closes[i-1]), abs(dec(bars[i]["l"]) - closes[i-1])) for i in range(len(bars)-14, len(bars))]
            output[interval] = {"bars": bars, "return": str(closes[-1] / closes[-2] - 1),
                "atr14": str(sum(tr) / 14), "sma20": str(sum(closes[-20:]) / 20), "sma50": str(sum(closes[-50:]) / 50)}
        return output


def fetch_news(cfg, *, session=None, now=None):
    now = time.time() if now is None else now
    session = session or requests.Session()
    with session.get(cfg.news_url, timeout=(5, 10), stream=True) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 2_000_000:
                raise ValueError("RSS too large")
            chunks.append(chunk)
    body = b"".join(chunks)
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("RSS entities forbidden")
    root = ET.fromstring(body)
    items, seen = [], set()
    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or "").strip()
        key = item.findtext("guid") or link
        if not link.startswith("https://") or not key or key in seen:
            continue
        published = None
        try:
            date = parsedate_to_datetime(item.findtext("pubDate") or "")
            published = date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp()
        except (ValueError, TypeError):
            pass
        if published is not None and not now - cfg.news_max_age_seconds <= published <= now:
            continue
        seen.add(key)
        items.append({"id": hashlib.sha256(key.encode()).hexdigest(), "url": link,
                      "title": (item.findtext("title") or "")[:500],
                      "summary": (item.findtext("description") or "")[:3000],
                      "published_at": published, "fetched_at": now})
    items.sort(key=lambda i: i["published_at"] or 0, reverse=True)
    return items[:cfg.news_max_items]
