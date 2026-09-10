"""将来情報を使わない、1分足ベースの仮想約定計算。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from ..models import Side, TradeDecision
from .data import CANDLE_INTERVAL_MS, NormalizedCandle, validate_contiguous_candles


class SimulationError(ValueError):
    """入力不足または時間順序違反で再現可能な計算ができない。"""


class IntrabarPolicy(str, Enum):
    STOP_FIRST = "stop_first"
    TAKE_FIRST = "take_first"


@dataclass(frozen=True)
class ExecutionConfig:
    model_delay_ms: int = 1_000
    max_arrival_delay_ms: int = 60_000
    max_hold_ms: int = 300_000
    fee_rate: Decimal = Decimal("0.00045")
    spread_bps: Decimal = Decimal("2")
    slippage_bps: Decimal = Decimal("1")
    intrabar_policy: IntrabarPolicy = IntrabarPolicy.STOP_FIRST

    def __post_init__(self) -> None:
        if min(self.model_delay_ms, self.max_arrival_delay_ms, self.max_hold_ms) < 0:
            raise SimulationError("delays and hold duration must be non-negative")
        if self.max_hold_ms == 0:
            raise SimulationError("max_hold_ms must be positive")
        if min(self.fee_rate, self.spread_bps, self.slippage_bps) < 0:
            raise SimulationError("execution costs must be non-negative")


@dataclass(frozen=True)
class SimulatedEpisode:
    decision_time_ms: int
    entry_time_ms: int
    exit_time_ms: int
    side: Side
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    exit_reason: str
    gross_pnl: Decimal
    fee: Decimal
    funding: Decimal
    net_pnl: Decimal
    quality: str


@dataclass
class VirtualAccount:
    """trade episodeだけを反映する、UTC日次境界付き仮想口座。"""

    equity: Decimal
    day_start_equity: Decimal
    daily_realized_pnl: Decimal = Decimal("0")
    peak_equity: Decimal | None = None
    utc_day: int | None = None

    def __post_init__(self) -> None:
        if self.equity <= 0 or self.day_start_equity <= 0:
            raise SimulationError("account equity must be positive")
        if self.peak_equity is None:
            self.peak_equity = self.equity

    def apply(self, episode: SimulatedEpisode, *, kind: str = "trade") -> None:
        if kind == "shadow":
            return
        if kind != "trade":
            raise SimulationError("episode kind must be trade or shadow")
        day = episode.exit_time_ms // 86_400_000
        if self.utc_day != day:
            self.utc_day = day
            self.day_start_equity = self.equity
            self.daily_realized_pnl = Decimal("0")
        self.equity += episode.net_pnl
        self.daily_realized_pnl += episode.net_pnl
        self.peak_equity = max(self.peak_equity or self.equity, self.equity)

    @property
    def drawdown_pct(self) -> Decimal:
        peak = self.peak_equity or self.equity
        return (peak - self.equity) / peak * Decimal("100")


def baseline_decision(name: str, *, return_5m: float) -> TradeDecision:
    """LLMを使わない比較基準を共通のTradeDecisionへ変換する。"""

    if name not in {"always_abstain", "momentum", "mean_reversion"}:
        raise SimulationError("unknown baseline")
    abstain = name == "always_abstain" or return_5m == 0
    signal = return_5m if name == "momentum" else -return_5m
    return TradeDecision(
        side=Side.LONG if signal >= 0 else Side.SHORT,
        stop_loss_pct=Decimal("0.30"),
        take_profit_pct=Decimal("0.60"),
        confidence=Decimal("0.5"),
        thesis=f"baseline:{name}",
        would_abstain=abstain,
        abstain_reason="baseline abstention" if abstain else None,
    )


def _execution_price(price: Decimal, *, side: Side, entering: bool, config: ExecutionConfig) -> Decimal:
    half_spread = config.spread_bps / Decimal("2")
    adverse_bps = half_spread + config.slippage_bps
    is_buy = (side is Side.LONG) == entering
    factor = Decimal("1") + (adverse_bps if is_buy else -adverse_bps) / Decimal("10000")
    return price * factor


def _find_entry(candles: list[NormalizedCandle], arrival_ms: int) -> NormalizedCandle:
    for candle in candles:
        if candle.open_time_ms >= arrival_ms:
            if candle.open_time_ms - arrival_ms > CANDLE_INTERVAL_MS:
                break
            return candle
    raise SimulationError("no entry candle within the arrival allowance")


def simulate_episode(
    *,
    decision: TradeDecision,
    decision_time_ms: int,
    quantity: Decimal,
    candles: list[NormalizedCandle],
    config: ExecutionConfig = ExecutionConfig(),
    funding: Decimal = Decimal("0"),
) -> SimulatedEpisode:
    """判断到着後の最初の足で入り、TP/SLまたは保有期限で決済する。

    OHLCだけでは同一足内の到達順を復元できないため、両方へ到達した場合は
    ``intrabar_policy``で結果を明示し、qualityをambiguous_intrabarにする。
    """

    if decision.would_abstain:
        raise SimulationError("abstention is not an account episode")
    if quantity <= 0:
        raise SimulationError("quantity must be positive")
    if not candles:
        raise SimulationError("candles are required")
    validate_contiguous_candles(candles)
    arrival_ms = decision_time_ms + config.model_delay_ms
    entry_candle = _find_entry(candles, arrival_ms)
    if entry_candle.open_time_ms - arrival_ms > config.max_arrival_delay_ms:
        raise SimulationError("entry arrived too late")

    raw_entry = Decimal(str(entry_candle.open))
    entry = _execution_price(raw_entry, side=decision.side, entering=True, config=config)
    stop_fraction = decision.stop_loss_pct / Decimal("100")
    take_fraction = decision.take_profit_pct / Decimal("100")
    if decision.side is Side.LONG:
        stop = raw_entry * (Decimal("1") - stop_fraction)
        take = raw_entry * (Decimal("1") + take_fraction)
    else:
        stop = raw_entry * (Decimal("1") + stop_fraction)
        take = raw_entry * (Decimal("1") - take_fraction)

    deadline = entry_candle.open_time_ms + config.max_hold_ms
    quality = "complete"
    raw_exit: Decimal | None = None
    exit_time = 0
    reason = ""
    for candle in candles[candles.index(entry_candle) :]:
        if candle.open_time_ms >= deadline:
            raw_exit = Decimal(str(candle.open))
            exit_time = candle.open_time_ms
            reason = "max_hold"
            break
        high, low = Decimal(str(candle.high)), Decimal(str(candle.low))
        hit_stop = low <= stop if decision.side is Side.LONG else high >= stop
        hit_take = high >= take if decision.side is Side.LONG else low <= take
        if hit_stop or hit_take:
            if hit_stop and hit_take:
                quality = "ambiguous_intrabar"
                hit_stop = config.intrabar_policy is IntrabarPolicy.STOP_FIRST
                hit_take = not hit_stop
            reason = "stop_loss" if hit_stop else "take_profit"
            trigger = stop if hit_stop else take
            # Stopのgapは不利なopenを使う。Takeの有利なgapはtrigger価格に固定する。
            if hit_stop:
                raw_exit = min(trigger, Decimal(str(candle.open))) if decision.side is Side.LONG else max(trigger, Decimal(str(candle.open)))
            else:
                raw_exit = trigger
            exit_time = candle.open_time_ms + CANDLE_INTERVAL_MS
            break
    if raw_exit is None:
        raise SimulationError("candles do not cover the complete holding period")

    exit_price = _execution_price(raw_exit, side=decision.side, entering=False, config=config)
    direction = Decimal("1") if decision.side is Side.LONG else Decimal("-1")
    gross = (exit_price - entry) * quantity * direction
    fee = (entry * quantity + exit_price * quantity) * config.fee_rate
    net = gross - fee + funding
    return SimulatedEpisode(
        decision_time_ms=decision_time_ms,
        entry_time_ms=entry_candle.open_time_ms,
        exit_time_ms=exit_time,
        side=decision.side,
        quantity=quantity,
        entry_price=entry,
        exit_price=exit_price,
        exit_reason=reason,
        gross_pnl=gross,
        fee=fee,
        funding=funding,
        net_pnl=net,
        quality=quality,
    )
