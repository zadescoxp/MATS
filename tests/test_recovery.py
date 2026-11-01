from framework.core.recovery import describe_error


def test_dns_failure_is_described_instead_of_the_request_url() -> None:
    cause = OSError("[Errno 8] nodename nor servname provided, or not known")
    resolution = OSError("Failed to resolve 'api.bybit.com'")
    resolution.__cause__ = cause
    visible = ConnectionError(
        "bybit GET https://api.bybit.com/v5/market/instruments-info?category=spot"
    )
    visible.__cause__ = resolution

    assert describe_error(visible) == (
        "Could not resolve api.bybit.com. DNS lookup failed, "
        "so the request never reached the exchange."
    )
