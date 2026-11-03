"""Which exchange errors are worth retrying.

ccxt names most temporary failures (network errors, timeouts, rate limits, exchange not
available). Bybit's "System error. Please try again later." (retCode 10016, e.g. "svc
error: Get kline failed") arrives as a generic ExchangeError, so it is matched by code.
"""

import re

# Bybit codes its docs say to retry: 10016 system error, 10006 too many requests.
_BYBIT_RETRYABLE = re.compile(r'"retCode"\s*:\s*(10016|10006)\b')
_TRANSIENT_NAMES = ("network", "timeout", "notavailable", "maintenance", "ratelimit", "ddos")


def is_transient_exchange_error(error: BaseException) -> bool:
    if "ccxt" not in type(error).__module__.lower():
        return False
    name = type(error).__name__.lower()
    return any(token in name for token in _TRANSIENT_NAMES) or bool(
        _BYBIT_RETRYABLE.search(str(error))
    )
