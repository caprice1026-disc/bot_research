from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import NamedTuple


class ReceiveStamp(NamedTuple):
    local_receive_ts: datetime
    local_monotonic_ns: int


def capture_receive_stamp() -> ReceiveStamp:
    wall_ns = time.time_ns()
    return ReceiveStamp(
        datetime.fromtimestamp(wall_ns / 1_000_000_000, tz=timezone.utc),
        time.monotonic_ns(),
    )
