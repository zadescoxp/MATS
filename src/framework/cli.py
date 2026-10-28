import argparse

from framework.core.graph import TradingFramework


def main() -> None:
    parser = argparse.ArgumentParser(description="Run or resume the MATS trading framework.")
    parser.add_argument("--symbol", help="Spot symbol, e.g. BTC/USDT.")
    parser.add_argument("--resume", nargs="?", const="", help="Resume a run (optionally by ID).")
    parser.add_argument("--timeframe", action="append", dest="timeframes")
    parser.add_argument("--account-equity", type=float)
    parser.add_argument("--enable-coder", action="store_true")
    parser.add_argument("--loop", action="store_true", help="Run repeated polling cycles.")
    parser.add_argument("--interval", type=float, default=60)
    arguments = parser.parse_args()

    framework = TradingFramework(enable_coder=arguments.enable_coder)
    try:
        if arguments.resume is not None:
            result = framework.resume(arguments.resume or None)
            print(result.model_dump_json(indent=2))
        elif arguments.loop:
            symbols = [arguments.symbol] if arguments.symbol else None
            for result in framework.run_polling(symbols, interval_seconds=arguments.interval):
                print(result.model_dump_json(indent=2))
        elif arguments.symbol:
            config = {}
            if arguments.timeframes:
                config["timeframes"] = arguments.timeframes
            if arguments.account_equity is not None:
                config["account_equity"] = arguments.account_equity
            result = framework.run(arguments.symbol, config)
            print(result.model_dump_json(indent=2))
        else:
            parser.error("--symbol or --resume is required unless --loop is selected")
    finally:
        framework.close()


if __name__ == "__main__":
    main()
