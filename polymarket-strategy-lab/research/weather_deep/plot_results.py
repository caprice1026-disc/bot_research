"""Static, exportable research figures from saved results only."""
from datetime import datetime
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".plot-deps"))
os.environ["MPLCONFIGDIR"] = str(ROOT / ".plot-deps/mplconfig")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

OUT = ROOT / "results/weather_deep"
FIG = OUT / "figures"
FIG.mkdir(exist_ok=True)
report = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
COLORS = {"chicago": "#176B92", "nyc": "#B66A24", "atlanta": "#3A8668"}
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.alpha": .2, "figure.dpi": 120, "savefig.dpi": 180})


def finish(fig, name):
    fig.tight_layout()
    for suffix in ("png", "svg"):
        fig.savefig(FIG / (name + "." + suffix), bbox_inches="tight")
    plt.close(fig)


def cumulative(rows):
    total, dates, values = 0, [], []
    for r in sorted(rows, key=lambda r: r["date"]):
        dates.append(datetime.fromisoformat(r["date"]))
        total += r["pnl"]
        values.append(total)
    return dates, values


fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
old = json.loads((ROOT / "results/weather_full_books/age_60_markup_0.01.json").read_text())
axes[0].plot(*cumulative(old["daily"]), color="#526580", linewidth=2)
axes[0].set_title("Prior Chicago: inspected 30-day sample")
axes[0].set_ylabel("Cumulative net PnL ($)")
for city in COLORS:
    primary = json.loads((OUT / city / "primary.json").read_text())
    axes[1].plot(*cumulative(primary["daily"]), color=COLORS[city], label="NYC" if city == "nyc" else city.capitalize(), linewidth=1.8)
joint = json.loads((OUT / "primary_daily.json").read_text())
axes[1].plot(*cumulative(joint), color="#262C32", label="New combined", linewidth=2.4)
axes[1].set_title("New sample: fixed rule, 72 city-days / 34 dates")
axes[1].legend(fontsize=9)
for ax in axes:
    ax.axhline(0, color="#777777", linewidth=.8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.set_xlabel("Weather target date (month-day)")
finish(fig, "cumulative_pnl")

fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
for city, color in COLORS.items():
    cases = report["city_results"][city]["scenarios"]
    for ax, key, values in ((axes[0], "age", [5, 15, 30, 60, 120, 300]), (axes[1], "latency", [1, 30, 60, 120, 300])):
        pnl = [cases["primary" if v == 60 else f"{key}_{v}"]["summary"]["net_pnl"] for v in values]
        ax.plot(values, pnl, "o-", color=color, label=city.capitalize())
        ax.set_xscale("log")
        ax.set_xticks(values, [str(v) for v in values])
        ax.axhline(0, color="#777777", linewidth=.8)
        ax.set_ylabel("Net PnL ($)")
axes[0].set_title("Quote-age sensitivity")
axes[0].set_xlabel("Maximum receive AND source age (s)")
axes[1].set_title("Execution-delay sensitivity")
axes[1].set_xlabel("Fixed delay after signal (s)")
axes[1].legend()
fig.suptitle("Exploratory one-factor comparisons; trade sets can change", fontsize=11)
finish(fig, "execution_sensitivity")

fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
for city, color in COLORS.items():
    cases = report["city_results"][city]["scenarios"]
    shares = [5, 10, 25, 50]
    fills = [cases["primary" if v == 5 else f"shares_{v}"]["summary"]["fills"] for v in shares]
    axes[0].plot(shares, fills, "o-", color=color, label=city.capitalize())
axes[0].set_title("Displayed-depth capacity at fixed share sizes")
axes[0].set_xlabel("Shares per order")
axes[0].set_ylabel("Conditional fills")
axes[0].set_xticks([5, 10, 25, 50])
axes[0].legend()
models = ["weighted", "gfs_global", "ecmwf_ifs025", "equal"]
names = ["Weighted", "GFS", "ECMWF", "Equal"]
for index, (city, color) in enumerate(COLORS.items()):
    values = [report["city_results"][city]["probability_diagnostics"][m]["mean_categorical_brier"] for m in models]
    axes[1].bar([i + (index - 1) * .24 for i in range(4)], values, .24, color=color, label=city.capitalize())
axes[1].set_xticks(range(4), names)
axes[1].set_ylabel("Categorical Brier score (lower is better)")
axes[1].set_title("Whole-event probability quality, not trading PnL")
finish(fig, "capacity_and_probability")
print("Saved six PNG/SVG figures under", FIG)
