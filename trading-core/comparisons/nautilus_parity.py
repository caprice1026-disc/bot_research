"""Offline parity fixture. Synthetic tape, NOT a market-profitability backtest."""
from __future__ import annotations
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
from decimal import Decimal as D
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from trading_core.accounting.models import AccountSnapshot
from trading_core.execution.position_plan import ExecutionCosts, PlanResult, RiskLimits
from trading_core.market_data.models import MarketTick
from trading_core.simulation.position_account import PositionAccount

# Common deterministic target policy: open, add, hold, reduce, close, short, close.
TAPE = [(1700000000000+i*60000, p, q) for i,(p,q) in enumerate([
    ("100.00","1.000"),("110.00","2.000"),("108.00","2.000"),
    ("120.00","1.000"),("90.00","0.000"),("95.00","-1.000"),("85.00","0.000")])]
FEE = D("0.001")
INITIAL = D("10000")

def normalized(ts, qty, price, fee):
    return {"timestamp_ms":int(ts), "signed_quantity":str(D(qty).normalize()),
            "price":str(D(price).normalize()), "fee":str(D(fee).normalize())}

def core_run(tape=TAPE):
    initial = AccountSnapshot(0,D(0),None,INITIAL,INITIAL,D(0),None,None,(),INITIAL,D(0),INITIAL)
    limits = RiskLimits(D(1000),D(1000),D(1),D(100),D(100),D(100),86400000)
    account = PositionAccount(initial, limits, ExecutionCosts(FEE,D(0),D(0)))
    fills=[]; current=D(0)
    for ts,p,target in tape:
        price=D(p); target=D(target)
        account.advance_to(MarketTick(ts,price,price,price,price))
        if target != current:
            plan=PlanResult("planned",None,target-current,abs(target)<abs(current),target,None,D(0),str(ts))
            for e in account.apply_plan(plan, executable_at_ms=ts):
                if e.kind=="fill": fills.append(normalized(e.timestamp_ms,e.quantity,e.price,-e.amount))
        current=target
    snap=account.snapshot(tape[-1][0])
    return {"fills":fills,"cash":str(snap.cash.normalize()),"fees":str(snap.fees_paid.normalize()),
            "gross_realized_pnl":str(snap.realized_pnl.normalize()),"quantity":str(snap.signed_quantity.normalize())}

def nautilus_run(tape=TAPE):
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.model.currencies import BTC, USDT
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.enums import OmsType, AccountType, OrderSide
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Price, Quantity, Money
    from nautilus_trader.trading.strategy import Strategy
    venue=Venue("SIM")
    instrument=CryptoPerpetual(InstrumentId.from_str("BTCUSDT-PERP.SIM"),Symbol("BTCUSDT"),BTC,USDT,USDT,
        False,2,3,Price.from_str("0.01"),Quantity.from_str("0.001"),0,0,
        margin_init=D("0.1"),margin_maint=D("0.05"),maker_fee=FEE,taker_fee=FEE)
    class TapeStrategy(Strategy):
        def __init__(self):
            super().__init__(); self.fills=[]; self.quantity=D(0); self.targets={t:D(q) for t,p,q in tape}
        def on_start(self): self.subscribe_quote_ticks(instrument.id)
        def on_quote_tick(self,tick):
            target=self.targets[tick.ts_event//1000000]; delta=target-self.quantity
            if delta:
                self.submit_order(self.order_factory.market(instrument.id,
                    OrderSide.BUY if delta>0 else OrderSide.SELL,Quantity.from_str(f"{abs(delta):.3f}"),
                    reduce_only=abs(target)<abs(self.quantity)))
        def on_order_filled(self,event):
            qty=event.last_qty.as_decimal()*(1 if event.order_side==OrderSide.BUY else -1)
            self.quantity+=qty
            self.fills.append(normalized(event.ts_event//1000000,qty,event.last_px.as_decimal(),event.commission.as_decimal()))
    engine=BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(venue,OmsType.NETTING,AccountType.MARGIN,[Money(INITIAL,USDT)],base_currency=USDT,
                     default_leverage=D(1),use_message_queue=False)
    engine.add_instrument(instrument)
    engine.add_data([QuoteTick(instrument.id,Price.from_str(p),Price.from_str(p),
        Quantity.from_str("100.000"),Quantity.from_str("100.000"),ts*1000000,ts*1000000) for ts,p,q in tape])
    strategy=TapeStrategy(); engine.add_strategy(strategy); engine.run()
    cash=engine.cache.account_for_venue(venue).balance_total(USDT).as_decimal()
    fees=sum(D(x["fee"]) for x in strategy.fills)
    result={"fills":strategy.fills,"cash":str(cash.normalize()),"fees":str(fees.normalize()),
            "gross_realized_pnl":str((cash-INITIAL+fees).normalize()),"quantity":str(strategy.quantity.normalize())}
    engine.dispose()
    return result

def compare():
    core=core_run(); nt=nautilus_run()
    assert core==nt, (core,nt)
    assert len(core["fills"])==6
    assert D(core["cash"])==D("10009.4")
    report={"fixture":"synthetic_zero_spread_linear_perp_target_tape", "data_sha256":hashlib.sha256(json.dumps(TAPE).encode()).hexdigest(),
      "python":platform.python_version(),"nautilus_trader":importlib.metadata.version("nautilus_trader"),
      "fee_rate":str(FEE),"status":"PASS", "core":core,"nautilus":nt,
      "not_validated":["real-market profitability","OHLC intrabar path","L2 depth or maker queues","latency",
        "funding","liquidation","inverse contracts","Polymarket settlement","live execution"]}
    return report

if __name__=="__main__":
    report=compare()
    if "--write" in sys.argv:
        Path(__file__).with_name("nautilus_parity_result.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
