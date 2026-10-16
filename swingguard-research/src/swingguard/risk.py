"""Explicit adaptation assumptions; thresholds are not paper-reported values."""
from decimal import Decimal


class RiskController:
    def __init__(self, config, equity):
        self.config = config
        self.state = "NORMAL"
        self.episode_peak = float(equity)
        self.recovery_reference = float(equity)
        self.entered_ms = 0
        self.quiet_since_ms = 0
        self.recovery_step = 0

    @property
    def cap(self):
        if self.state == "SURVIVAL":
            return Decimal("0")
        if self.state == "ALERT":
            return Decimal(self.config["alert_cap"])
        if self.state == "RECOVERY":
            return Decimal(self.config["recovery_caps"][self.recovery_step])
        return Decimal("1")

    def update(self, timestamp, equity, day_equity, market_drop):
        c, eq = self.config, float(equity)
        self.episode_peak = max(self.episode_peak, eq)
        dd = max(0, 1 - eq / self.episode_peak)
        daily_loss = max(0, 1 - eq / float(day_equity))
        shock = max(0, float(market_drop))
        alert = dd >= c["alert_dd"] or daily_loss >= c["alert_daily_loss"] or shock >= c["alert_market_drop_1h"]
        acute = daily_loss >= c["survival_daily_loss"] or shock >= c["survival_market_drop_1h"]
        quiet = daily_loss < c["alert_daily_loss"] / 2 and shock < c["alert_market_drop_1h"] / 2
        before = self.state
        reason = "unchanged"
        if self.state in ("NORMAL", "ALERT"):
            if acute or dd >= c["survival_dd"]:
                self.state, self.entered_ms = "SURVIVAL", timestamp
                reason = "survival_threshold"
            elif alert:
                self.state = "ALERT"
                reason = "alert_threshold"
            elif dd < c["alert_dd"] / 2 and quiet:
                self.state = "NORMAL"
                reason = "quiet_normal"
        elif self.state == "SURVIVAL":
            if timestamp - self.entered_ms >= c["survival_min_ms"] and quiet:
                self.state = "RECOVERY"
                self.recovery_step = 0
                self.recovery_reference = eq
                self.quiet_since_ms = timestamp
                reason = "cooldown_complete"
        else:
            redrop = max(0, 1 - eq / self.recovery_reference)
            if acute or redrop >= c["recovery_redrop"]:
                self.state, self.entered_ms = "SURVIVAL", timestamp
                reason = "recovery_failed"
            elif not quiet:
                self.quiet_since_ms = timestamp
            elif timestamp - self.quiet_since_ms >= c["recovery_step_ms"]:
                self.recovery_step += 1
                self.quiet_since_ms = timestamp
                if self.recovery_step >= len(c["recovery_caps"]):
                    self.state = "NORMAL"
                    self.episode_peak = eq
                reason = "recovery_step"
        score = max(dd / c["survival_dd"], daily_loss / c["survival_daily_loss"], shock / c["survival_market_drop_1h"])
        return {"state": self.state, "cap": str(self.cap), "episode_drawdown": dd,
                "daily_loss": daily_loss, "market_drop_1h": shock, "adaptation_score": score,
                "changed": before != self.state, "reason": reason,
                "recovery_step": self.recovery_step if self.state == "RECOVERY" else None}
