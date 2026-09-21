"""One scheduled cycle, with durable checkpoints before and after external calls."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import os
import time
from .config import dec
from .domain import account, advance, enforce, equity, execute, plan, track_risk
from .market import fetch_news
from .policy import validate_review
from .store import Lease, Conflict


def initial_state(cfg, now):
    return {"schema": 1, "bot_id": cfg.bot_id, "started_at": now, "last_cycle": -1,
            "cursor": int(now // 60) * 60, "accounts": {name: account(cfg.initial_equity) for name in ("llm", "cash", "btc", "trend")},
            "strategy": None, "strategy_version": 0, "last_review": None, "last_review_attempt": None,
            "last_mark": None, "config_hash": cfg.fingerprint(), "code_commit": os.environ.get("CODE_COMMIT", "local"),
            "evaluation_segment_started_at": now, "pending_event": None, "manual_pause": False,
            "last_error": None, "evaluation_30d": None, "recent_decisions": [], "config_changes": 0}


class Runner:
    def __init__(self, cfg, store, market, policy, *, news=fetch_news, clock=time.time):
        self.cfg, self.store, self.market, self.policy = cfg, store, market, policy
        self.news, self.clock = news, clock

    def fresh(self, market):
        age = self.clock() - market["at"]
        if age < -2 or age > self.cfg.book_max_age_seconds:
            raise ValueError("stale book")

    def flush(self, lease):
        event = lease.data.get("pending_event")
        if event:
            key = event["id"]
            existing, version = self.store.read(key)
            if existing is None:
                self.store.write(key, event, version)
            elif existing != event:
                raise Conflict("event mismatch")
            lease.data["pending_event"] = None
            lease.save()
        for key, boundary in list(lease.data.get("pending_boundaries", {}).items()):
            existing, version = self.store.read(key)
            if existing is None:
                self.store.write(key, boundary, version)
            del lease.data["pending_boundaries"][key]
            lease.save()

    def checkpoint(self, lease, event, phase):
        # State and its audit event are atomic in runtime, then copied idempotently.
        self.flush(lease)
        record = deepcopy(event)
        record.update(id=event["base_id"] + "-" + phase, phase=phase, accounts=deepcopy(lease.data["accounts"]))
        lease.data["pending_event"] = record
        lease.save()
        self.flush(lease)

    def settle(self, data, market, event):
        working, audit = deepcopy(data), deepcopy(event)
        self._settle(working, market, audit)
        data.clear()
        data.update(working)
        event.clear()
        event.update(audit)

    def _settle(self, data, market, event):
        now = self.clock()
        until = int(now // 60) * 60
        if until > data["cursor"]:
            # Bound one request/document. Long outages remain partial rather than silently truncating.
            since = max(data["cursor"], until - 3600)
            if since != data["cursor"]:
                for a in data["accounts"].values():
                    a["partial"] = True
                    a["gap_since"] = a["gap_since"] or data["cursor"]
            bars, funding = self.market.history(since, until)
            event.setdefault("market_intervals", []).append({"since": data["cursor"], "until": until, "bars": bars, "funding": funding})
            for name, a in data["accounts"].items():
                if name != "cash":
                    additions = advance(a, bars, funding, since, until, self.cfg, protected=name != "btc")
                    event["events"].extend({**e, "account": name} for e in additions)
            if bars and datetime.fromtimestamp(until, timezone(timedelta(hours=9))).strftime("%H:%M") == "00:00":
                key = "boundary-" + datetime.fromtimestamp(until, timezone(timedelta(hours=9))).date().isoformat()
                data.setdefault("pending_boundaries", {})[key] = {"at": until, "equities": {n: str(equity(a, bars[-1]["c"])) for n, a in data["accounts"].items()},
                                         "status": "partial" if any(a["partial"] for a in data["accounts"].values()) else "complete"}
            data["cursor"] = until
        for name in ("llm", "trend"):
            a = data["accounts"][name]
            track_risk(a, market["mark"], now, self.cfg)
            event["events"].extend({**e, "account": name} for e in enforce(a, market, now, self.cfg))
            track_risk(a, market["mark"], now, self.cfg)
        data["last_mark"] = market["mark"]

    def run(self, scheduled_at):
        now = self.clock()
        if scheduled_at > now + 2 or int(scheduled_at) % 300:
            raise ValueError("scheduled_at must be a nonfuture five-minute boundary")
        cfg = self.cfg
        lease = Lease(self.store, initial_state(cfg, now), clock=self.clock, duration=300)
        data = lease.data
        deadline = time.monotonic() + cfg.cycle_timeout_seconds
        day = datetime.fromtimestamp(scheduled_at, timezone.utc).strftime("%Y%m%d")
        event = {"base_id": f"cycle-{day}-{int(scheduled_at)}", "scheduled_at": scheduled_at, "at": now,
                 "config_hash": cfg.fingerprint(), "events": [], "status": "started"}
        try:
            if data["schema"] != 1 or data["bot_id"] != cfg.bot_id:
                raise ValueError("runtime schema or identity mismatch")
            self.flush(lease)
            if scheduled_at <= data["last_cycle"]:
                return {"status": "duplicate"}
            commit = os.environ.get("CODE_COMMIT", "local")
            if data["config_hash"] != cfg.fingerprint() or data.get("code_commit") != commit:
                event["events"].append({"kind": "config_changed", "previous": data["config_hash"], "current": cfg.public()})
                data.update(config_hash=cfg.fingerprint(), code_commit=commit, evaluation_segment_started_at=now,
                            config_changes=data.get("config_changes", 0) + 1)
            data["last_cycle"] = scheduled_at
            lease.save()  # Claim even if market acquisition or a paid call fails later.
            market = self.market.snapshot()
            self.fresh(market)
            event["observation"] = market
            self.settle(data, market, event)
            self.checkpoint(lease, event, "risk")
            features = self.market.features(now)
            event["features"] = features
            fresh_strategy = data["last_review"] is not None and now - data["last_review"] <= cfg.strategy_ttl_seconds
            timely = now - scheduled_at <= cfg.max_start_delay_seconds
            a = data["accounts"]["llm"]
            if timely and fresh_strategy and not data["manual_pause"] and not a["partial"] and time.monotonic() + cfg.trader_timeout_seconds + 20 < deadline:
                version = a["version"]
                inputs = {"market": market, "features": features, "account": deepcopy(a), "equity": str(equity(a, market["mark"])),
                          "strategy": data["strategy"], "recent_decisions": data.get("recent_decisions", [])[-6:],
                          "risk": {"max_exposure": str(cfg.max_exposure), "stop_risk": str(cfg.stop_risk)}}
                request_id = f"trader-{int(scheduled_at)}"
                try:
                    raw = self.policy.call("trader", request_id, inputs)
                    event["proposal"] = raw
                    after = self.market.snapshot()
                    self.fresh(after)
                    original_mark = market["mark"]
                    market = after
                    self.settle(data, after, event)
                    a = data["accounts"]["llm"]
                    self.checkpoint(lease, event, "post-response-risk")
                    if a["version"] != version:
                        event["decision_status"] = "stale_position"
                    elif time.monotonic() + 15 >= deadline:
                        event["decision_status"] = "deadline"
                    else:
                        proposed = plan(a, after["mark"], after["step"], raw, cfg,
                                        allow_increase=self.clock() - data["last_review"] <= cfg.strategy_ttl_seconds, observed_mark=original_mark)
                        event["plan"] = proposed
                        event["events"].extend({**e, "account": "llm"} for e in execute(a, proposed, after, self.clock(), cfg))
                        event["events"].extend({**e, "account": "llm"} for e in enforce(a, after, self.clock(), cfg))
                        event["decision_status"] = "applied"
                        market = after
                except Exception as exc:
                    if isinstance(exc, Conflict):
                        raise
                    event["decision_status"] = type(exc).__name__
                self.checkpoint(lease, event, "decision")
            else:
                event["decision_status"] = "paused_or_stale_or_late"
            if timely and not data["manual_pause"]:
                self.baselines(data, market, features, event)
            self.checkpoint(lease, event, "trading")
            data["recent_decisions"] = (data.get("recent_decisions", []) + [{"at": now, "status": event["decision_status"],
                "proposal": event.get("proposal"), "plan": event.get("plan"), "events": event["events"][-20:]}])[-24:]
            due = data["last_review"] is None or now - data["last_review"] >= cfg.review_interval_seconds
            if due and time.monotonic() + cfg.reviewer_timeout_seconds + 20 < deadline:
                data["last_review_attempt"] = now
                lease.save()
                try:
                    try:
                        news = self.news(cfg, now=self.clock())
                        news_status = "ok"
                    except Exception as exc:
                        news, news_status = [], "unavailable"
                        event["news_error"] = type(exc).__name__
                    seen, _ = self.store.read("news-seen")
                    seen = seen or {"items": {}}
                    recent = {k: t for k, t in seen["items"].items() if now - t < cfg.news_max_age_seconds * 2}
                    selected = [n for n in news if n["id"] not in recent]
                    inputs = {"market": market, "features": features, "accounts": data["accounts"],
                              "strategy": data["strategy"], "news": selected, "news_status": news_status,
                              "recent_decisions": data["recent_decisions"]}
                    raw = self.policy.call("reviewer", f"reviewer-{int(scheduled_at)}", inputs)
                    strategy = validate_review(raw, selected)
                    data["strategy_version"] += 1
                    data["strategy"] = {**strategy, "version": data["strategy_version"], "updated_at": self.clock()}
                    data["last_review"] = self.clock()
                    event["review"] = data["strategy"]
                    event["news"] = selected
                    recent.update({n["id"]: now for n in selected})
                    _, nv = self.store.read("news-seen")
                    self.store.write("news-seen", {"items": recent}, nv)
                except Exception as exc:
                    if isinstance(exc, Conflict):
                        raise
                    event["review_error"] = type(exc).__name__
            for name in ("llm", "trend"):
                track_risk(data["accounts"][name], market["mark"], self.clock(), cfg)
            event["equities"] = {n: str(equity(a, market["mark"])) for n, a in data["accounts"].items()}
            event["status"] = "partial" if any(a["partial"] for a in data["accounts"].values()) else "complete"
            if data["evaluation_30d"] is None and now - data["started_at"] >= 30 * 86400:
                data["evaluation_30d"] = {"at": now, "equities": event["equities"], "accounts": deepcopy(data["accounts"]),
                    "risk_pass": dec(data["accounts"]["llm"]["max_dd"]) <= cfg.evaluation_dd,
                    "profitability": "pending_cost_reconciliation", "status": event["status"],
                    "configuration_fixed": data.get("config_changes", 0) == 0}
            data["last_error"] = None
            self.checkpoint(lease, event, "done")
            return {"status": event["status"], "decision": event["decision_status"], "review_version": data["strategy_version"]}
        except Conflict:
            raise
        except Exception as exc:
            data["last_error"] = type(exc).__name__
            event.update(status="error", error_type=type(exc).__name__)
            self.checkpoint(lease, event, "error")
            return {"status": "error", "error_type": type(exc).__name__}
        finally:
            lease.release()

    def baselines(self, data, market, features, event):
        cfg = self.cfg
        btc = data["accounts"]["btc"]
        if btc["fills"] == 0 and not btc["partial"]:
            from .domain import floor_qty
            target = floor_qty(cfg.initial_equity / dec(market["asks"][0]["px"]) / (1 + cfg.fee_rate + cfg.slippage_bps / 10000), market["step"])
            event["events"].extend({**e, "account": "btc"} for e in execute(btc, {"target": str(target), "stop": None, "reason": "buy_hold"}, market, self.clock(), cfg))
        trend = data["accounts"]["trend"]
        f = features["1h"]
        direction = 1 if dec(f["sma20"]) > dec(f["sma50"]) else -1 if dec(f["sma20"]) < dec(f["sma50"]) else 0
        stop = dec(market["mark"]) - direction * 2 * dec(f["atr14"])
        raw = {"intent": "set_target", "target_multiple": str(direction), "stop_price": str(stop) if direction else None, "reason": "sma20_50_1h_atr14x2_v1"}
        if not trend["partial"] and not data["manual_pause"]:
            proposed = plan(trend, market["mark"], market["step"], raw, cfg)
            event["events"].extend({**e, "account": "trend"} for e in execute(trend, proposed, market, self.clock(), cfg))
