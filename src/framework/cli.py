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
        help="Run all configured symbols (MATS_SYMBOLS); live testnet runs are sequential.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        help="Maximum concurrent research/dry-run symbol runs; live runs are sequential.",
    )
    parser.add_argument("--resume", nargs="?", const="", help="Resume a run (optionally by ID).")
    parser.add_argument("--timeframe", action="append", dest="timeframes")
    parser.add_argument("--account-equity", type=float)
    parser.add_argument(
        "--max-attempts",
        type=int,
        choices=range(1, 21),
        metavar="N",
        help="Strategy attempts before a run is marked rejected (default from settings).",
    )
    parser.add_argument(
        "--extra-attempts",
        type=int,
        choices=range(1, 11),
        metavar="N",
        help="With --resume RUN_ID, give a rejected run N more strategy attempts.",
    )
    parser.add_argument("--enable-coder", action="store_true")
    parser.add_argument("--loop", action="store_true", help="Run repeated polling cycles.")
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument(
        "--monitor-open-positions",
        action="store_true",
        help="Evaluate and flatten persisted open positions once without starting new runs.",
    )
    parser.add_argument(
        "--reset-stopped-strategies",
        action="store_true",
        help="Clear stopped-strategy checkpoints for the selected symbol(s).",
    )
    arguments = parser.parse_args()

    framework = TradingFramework(enable_coder=arguments.enable_coder)
    exit_code = 0
    try:
        if arguments.monitor_open_positions and (
            arguments.resume is not None or arguments.loop or arguments.symbols or arguments.all
        ):
            parser.error("--monitor-open-positions cannot be combined with a run command")
        if arguments.reset_stopped_strategies:
            if arguments.all and arguments.symbols:
                parser.error("--all and --symbol cannot be used together")
            reset_symbols = (
                framework.settings.symbols
                if arguments.all or not arguments.symbols
                else arguments.symbols
            )
            print(framework.reset_stopped_strategies(reset_symbols))
        if arguments.monitor_open_positions:
            print(framework.monitor_open_positions())
        elif arguments.resume is not None:
            result = framework.resume(
                arguments.resume or None, extra_attempts=arguments.extra_attempts
            )
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
                    **(
                        {"max_revision_loops": arguments.max_attempts - 1}
                        if arguments.max_attempts is not None
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
            if arguments.max_attempts is not None:
                config["max_revision_loops"] = arguments.max_attempts - 1
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
