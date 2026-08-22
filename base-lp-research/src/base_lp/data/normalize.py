from __future__ import annotations

from base_lp.data.rpc import hex_to_int, normalize_address
from base_lp.schemas import LogRecord


def log_record_from_rpc(raw: dict[str, object], timestamp: int) -> LogRecord:
    def value(camel: str, snake: str) -> object:
        if camel in raw:
            return raw[camel]
        if snake in raw:
            return raw[snake]
        raise KeyError(camel)

    return LogRecord(
        block_number=hex_to_int(value("blockNumber", "block_number")),
        transaction_index=hex_to_int(value("transactionIndex", "transaction_index")),
        log_index=hex_to_int(value("logIndex", "log_index")),
        block_hash=str(value("blockHash", "block_hash")),
        transaction_hash=str(value("transactionHash", "transaction_hash")),
        address=normalize_address(str(value("address", "address"))),
        topics=[str(topic).lower() for topic in value("topics", "topics")],
        data=str(value("data", "data")).lower(),
        timestamp=timestamp,
    )
