"""Daily summaries, transition notifications and verified archive export."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import time
import requests
from .config import dec
from .domain import equity

JST = timezone(timedelta(hours=9))


def percentile(values, p):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * p) - 1)] if values else None


def daily_summary(store, day, cfg):
    start = datetime.fromisoformat(day).replace(tzinfo=JST).timestamp()
    end = start + 86400
    records = []
    for date in {datetime.fromtimestamp(start, timezone.utc).strftime("%Y%m%d"), datetime.fromtimestamp(end-1, timezone.utc).strftime("%Y%m%d")}:
        records.extend(v for k, v, _ in store.items("cycle-" + date) if k.endswith("-done") and start <= v["scheduled_at"] < end)
    records.sort(key=lambda r: r["scheduled_at"])
    calls = []
    keys = [f"llm-{role}-{slot}" for slot in range(int(start), int(end), 300) for role in ("trader", "reviewer")]
    calls = [row for row in store.read_many(keys).values() if row]
    role_stats = {}
    for role in ("trader", "reviewer"):
        selected = [c for c in calls if c["role"] == role]
        measured = [c["latency_seconds"] for c in selected if "latency_seconds" in c]
        role_stats[role] = {"calls": len(selected), "failures": sum(c["status"] != "received" for c in selected),
            "median_seconds": percentile(measured, .5), "p95_seconds": percentile(measured, .95),
            "max_seconds": max(measured) if measured else None,
            "estimated_usd": str(sum((dec(c["estimated_usd"]) for c in selected), dec(0)))}
    costs = sum((dec(c["estimated_usd"]) for c in calls), dec(0))
    cloud, _ = store.read("cloud-cost-" + day)
    result = {"day_jst": day, "complete_cycles": len(records), "expected_cycles": 288,
              "data_status": "complete" if len(records) == 288 and all(r["status"] == "complete" for r in records) else "partial",
              "llm": role_stats, "cloud_cost_usd": cloud["usd"] if cloud else None,
              "cloud_cost_status": cloud["source"] if cloud else "unavailable", "net_profit_status": "pending"}
    if records:
        # First sample is after first cycle, not exact midnight: never call this full-day realized PnL.
        result["ending_equities"] = records[-1]["equities"]
        result["accounts"] = records[-1]["accounts"]
        result["llm_position"] = {k: records[-1]["accounts"]["llm"][k] for k in ("quantity", "stop", "max_dd", "stop_count", "fills", "fees", "funding")}
        result["daily_fills"] = sum(e.get("kind") == "fill" and e.get("account") == "llm" for r in records for e in r["events"])
        result["sample_to_sample_pnl"] = str(dec(records[-1]["equities"]["llm"]) - dec(records[0]["equities"]["llm"]))
        result["sample_to_sample_net_estimate"] = str(dec(result["sample_to_sample_pnl"]) - costs - (dec(cloud["usd"]) if cloud else 0)) if cloud else None
        result["timing_note"] = "start/end sampled after cycles; not exact midnight marks"
    beginning, _ = store.read("boundary-" + day)
    ending, _ = store.read("boundary-" + datetime.fromtimestamp(end, JST).date().isoformat())
    if beginning and ending:
        result["daily_pnl"] = str(dec(ending["equities"]["llm"]) - dec(beginning["equities"]["llm"]))
        result["daily_net_estimate"] = str(dec(result["daily_pnl"]) - costs - dec(cloud["usd"])) if cloud else None
        result["timing_note"] = "midnight minute-close marks; fees/funding included; API/cloud deducted separately"
        result["net_profit_status"] = "estimated" if cloud else "pending_cloud_cost"
    return result


def render_daily(bot_id, summary):
    position = summary.get("llm_position", {})
    lines = [f"{bot_id} / {summary['day_jst']} JST", f"data={summary['data_status']} cycles={summary['complete_cycles']}/288",
             f"equities={summary.get('ending_equities', {})}",
             f"PnL={summary.get('daily_pnl', 'unavailable')} net_estimate={summary.get('daily_net_estimate', 'unavailable')}",
             f"position={position.get('quantity')} SL={position.get('stop')} DD={position.get('max_dd')} stops={position.get('stop_count')} fills_today={summary.get('daily_fills')}",
             f"cloud_cost={summary.get('cloud_cost_usd')} ({summary['cloud_cost_status']})"]
    for role, stats in summary["llm"].items():
        lines.append(f"{role}: calls={stats['calls']} failures={stats['failures']} median={stats['median_seconds']}s p95={stats['p95_seconds']}s max={stats['max_seconds']}s cost_estimate=${stats['estimated_usd']}")
    return "\n".join(lines)


def notify(store, webhook, now, cfg, *, session=None):
    if not webhook:
        return
    state, _ = store.read("runtime")
    if not state:
        return
    month = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m")
    budget, _ = store.read("budget-" + month)
    conditions = {"runtime_error": state.get("last_error"), "manual_pause": state.get("manual_pause"),
                  "dd_stop": state["accounts"]["llm"]["dd_stopped_at"] is not None,
                  "partial_data": any(a["partial"] for a in state["accounts"].values()),
                  "strategy_expired": state["last_review"] is None or now - state["last_review"] > cfg.strategy_ttl_seconds,
                  "budget_exceeded": bool(budget and dec(budget["estimated_usd"]) >= cfg.llm_monthly_budget_usd)}
    previous, pv = store.read("notification-state")
    previous = previous or {}
    changed = {k: v for k, v in conditions.items() if previous.get(k) != v}
    if changed:
        key = "notice-" + hashlib.sha256(json.dumps([previous, conditions], sort_keys=True).encode()).hexdigest()[:24] + "-" + str(int(now))
        send_once(store, webhook, key, f"{cfg.bot_id}: {json.dumps(changed, ensure_ascii=False)}", session=session)
        store.write("notification-state", conditions, pv)
    local = datetime.fromtimestamp(now, JST)
    if local.hour >= 9:
        day = (local.date() - timedelta(days=1)).isoformat()
        key = "daily-" + day
        saved, _ = store.read(key)
        if not saved:
            summary = daily_summary(store, day, cfg)
            store.write(key, {"at": now, "summary": summary}, 0)
        else:
            summary = saved["summary"]
        send_once(store, webhook, "notice-" + key, render_daily(cfg.bot_id, summary), session=session)


def send_once(store, webhook, key, text, *, session=None):
    """Persist intent first; ambiguous sends require operator inspection, never blind replay."""
    old, version = store.read(key)
    if old:
        return
    version = store.write(key, {"at": time.time(), "status": "sending"}, version)
    session = session or requests.Session()
    try:
        response = session.post(webhook, params={"wait": "true"}, json={"content": text[:1950], "allowed_mentions": {"parse": []}}, timeout=(5, 10))
        response.raise_for_status()
        message_id = response.json().get("id")
        store.write(key, {"at": time.time(), "status": "sent", "message_id": message_id}, version)
    except Exception:
        store.write(key, {"at": time.time(), "status": "unknown"}, version)
        raise RuntimeError("Discord delivery unknown; inspect channel before retry") from None


def archive(store, bucket, bot_id, *, now=None, retain_days=30):
    """Keep runtime/unknown orders indefinitely; delete only verified, terminal history."""
    now = time.time() if now is None else now
    count = 0
    for prefix in ("cycle-", "llm-", "daily-", "notice-"):
        for key, row, version in store.items(prefix):
            if row.get("at", now) >= now - retain_days * 86400 or row.get("status") in {"unknown", "sending"}:
                continue
            payload = json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
            checksum = hashlib.sha256(payload).hexdigest()
            blob = bucket.blob(f"{bot_id}/{key}/{checksum}.json")
            if not blob.exists():
                blob.upload_from_string(payload, content_type="application/json", if_generation_match=0)
            if hashlib.sha256(blob.download_as_bytes()).hexdigest() != checksum:
                raise ValueError("archive verification failed; source retained")
            store.delete(key, version)
            count += 1
    return count
