import argparse

from framework.core.graph import TradingFramework


def main() -> None:
    parser = argparse.ArgumentParser(description="Run or resume the MATS trading framework.")
    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help="Spot symbol, e.g. BTC/USDT. Repeat for concurrent multi-symbol research.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all configured symbols concurrently (MATS_SYMBOLS).",
    )
    parser.add_argument("--workers", type=int, help="Maximum concurrent symbol runs.")
    parser.add_argument("--resume", nargs="?", const="", help="Resume a run (optionally by ID).")
    parser.add_argument("--timeframe", action="append", dest="timeframes")
    parser.add_argument("--account-equity", type=float)
    parser.add_argument("--enable-coder", action="store_true")
    parser.add_argument("--loop", action="store_true", help="Run repeated polling cycles.")
    parser.add_argument("--interval", type=float, default=60)
    arguments = parser.parse_args()

    framework = TradingFramework(enable_coder=arguments.enable_coder)
    exit_code = 0
    try:
        if arguments.resume is not None:
            result = framework.resume(arguments.resume or None)
            print(result.model_dump_json(indent=2))
            if result.status == "failed":
                exit_code = 1
        elif arguments.loop:
            if arguments.all and arguments.symbols:
                parser.error("--all and --symbol cannot be used together")
            symbols = framework.settings.symbols if arguments.all else arguments.symbols
            for result in framework.run_polling(
                symbols,
                interval_seconds=arguments.interval,
                max_workers=arguments.workers,
            ):
                print(result.model_dump_json(indent=2))
                if result.status == "failed":
                    exit_code = 1
        elif arguments.all or arguments.symbols and len(arguments.symbols) > 1:
            if arguments.all and arguments.symbols:
                parser.error("--all and --symbol cannot be used together")
            if arguments.timeframes:
                config = {"timeframes": arguments.timeframes}
            else:
                config = None
            symbols = framework.settings.symbols if arguments.all else arguments.symbols
            results = framework.run_many(
                symbols,
                {
                    **(config or {}),
                    **(
                        {"account_equity": arguments.account_equity}
                        if arguments.account_equity is not None
                        else {}
                    ),
                },
                max_workers=arguments.workers,
            )
            print(
                "[\n" + ",\n".join(result.model_dump_json(indent=2) for result in results) + "\n]"
            )
            if any(result.status == "failed" for result in results):
                exit_code = 1
        elif arguments.symbols:
            if arguments.all:
                parser.error("--all and --symbol cannot be used together")
            config = {}
            if arguments.timeframes:
                config["timeframes"] = arguments.timeframes
            if arguments.account_equity is not None:
                config["account_equity"] = arguments.account_equity
            result = framework.run(arguments.symbols[0], config)
            print(result.model_dump_json(indent=2))
            if result.status == "failed":
                exit_code = 1
        else:
            parser.error("--symbol, --all, or --resume is required unless --loop is selected")
    finally:
        framework.close()
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
