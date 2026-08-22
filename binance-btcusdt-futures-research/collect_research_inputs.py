from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from btc_regime_eda.collection import collect_research_inputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect verified inputs for BTC regime EDA")
    parser.add_argument("--config", default="configs/research.json")
    args = parser.parse_args()
    config = json.loads((ROOT / args.config).read_text(encoding="utf-8"))
    try:
        manifest = collect_research_inputs(config, ROOT)
    except Exception as error:
        print(f"collection_error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"datasets": manifest["datasets"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
