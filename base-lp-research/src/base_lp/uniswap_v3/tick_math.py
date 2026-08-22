from __future__ import annotations


MIN_TICK = -887272
MAX_TICK = 887272
MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342

_RATIOS = (
    0xFFFcb933BD6fad37aa2d162d1a594001,
    0xFFF97272373d413259A46990580e213A,
    0xFFF2E50F5F656932EF12357CF3C7FDCC,
    0xFFE5CACA7E10E4E61C3624EAA0941CD0,
    0xFFCB9843D60F6159C9DB58835C926644,
    0xFF973B41FA98C081472E6896DFB254C0,
    0xFF2EA16466C96A3843EC78B326B52861,
    0xFE5DEE046A99A2A811C461F1969C3053,
    0xFCBE86C7900A88AEDCFFC83B479AA3A4,
    0xF987A7253AC413176F2B074CF7815E54,
    0xF3392B0822B70005940C7A398E4B70F3,
    0xE7159475A2C29B7443B29C7FA6E889D9,
    0xD097F3BDFD2022B8845AD8F792AA5825,
    0xA9F746462D870FDF8A65DC1F90E061E5,
    0x70D869A156D2A1B890BB3DF62BAF32F7,
    0x31BE135F97D08FD981231505542FCFA6,
    0x9AA508B5B7A84E1C677DE54F3E99BC9,
    0x5D6AF8DEDB81196699C329225EE604,
    0x2216E584F5FA1EA926041BEDFE98,
    0x48A170391F7DC42444E8FA2,
)


def get_sqrt_ratio_at_tick(tick: int) -> int:
    if tick < MIN_TICK or tick > MAX_TICK:
        raise ValueError(f"tick outside [{MIN_TICK}, {MAX_TICK}]: {tick}")
    absolute = -tick if tick < 0 else tick
    ratio = _RATIOS[0] if absolute & 1 else 1 << 128
    for bit, multiplier in enumerate(_RATIOS[1:], start=1):
        if absolute & (1 << bit):
            ratio = (ratio * multiplier) >> 128
    if tick > 0:
        ratio = ((1 << 256) - 1) // ratio
    return (ratio >> 32) + (1 if ratio & ((1 << 32) - 1) else 0)


def get_tick_at_sqrt_ratio(sqrt_price_x96: int) -> int:
    if sqrt_price_x96 < MIN_SQRT_RATIO or sqrt_price_x96 >= MAX_SQRT_RATIO:
        raise ValueError("sqrt price outside TickMath domain")
    low = MIN_TICK
    high = MAX_TICK + 1
    while high - low > 1:
        middle = (low + high) // 2
        if get_sqrt_ratio_at_tick(middle) <= sqrt_price_x96:
            low = middle
        else:
            high = middle
    return low
