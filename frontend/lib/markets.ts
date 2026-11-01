export interface Market {
  symbol: string;
  name: string;
}

export const MARKETS: Market[] = [
  { symbol: "BTC/USDT", name: "Bitcoin" },
  { symbol: "ETH/USDT", name: "Ethereum" },
  { symbol: "SOL/USDT", name: "Solana" },
  { symbol: "BNB/USDT", name: "BNB" },
  { symbol: "XRP/USDT", name: "XRP" },
  { symbol: "DOGE/USDT", name: "Dogecoin" },
  { symbol: "ADA/USDT", name: "Cardano" },
  { symbol: "AVAX/USDT", name: "Avalanche" },
  { symbol: "LINK/USDT", name: "Chainlink" },
  { symbol: "DOT/USDT", name: "Polkadot" },
  { symbol: "TRX/USDT", name: "Tron" },
  { symbol: "TON/USDT", name: "Toncoin" },
  { symbol: "LTC/USDT", name: "Litecoin" },
  { symbol: "BCH/USDT", name: "Bitcoin Cash" },
  { symbol: "ATOM/USDT", name: "Cosmos" },
  { symbol: "NEAR/USDT", name: "NEAR" },
  { symbol: "APT/USDT", name: "Aptos" },
  { symbol: "SUI/USDT", name: "Sui" },
  { symbol: "SEI/USDT", name: "Sei" },
  { symbol: "INJ/USDT", name: "Injective" },
  { symbol: "TIA/USDT", name: "Celestia" },
  { symbol: "FIL/USDT", name: "Filecoin" },
  { symbol: "ICP/USDT", name: "Internet Computer" },
  { symbol: "HBAR/USDT", name: "Hedera" },
  { symbol: "ETC/USDT", name: "Ethereum Classic" },
  { symbol: "UNI/USDT", name: "Uniswap" },
  { symbol: "AAVE/USDT", name: "Aave" },
  { symbol: "MKR/USDT", name: "Maker" },
  { symbol: "LDO/USDT", name: "Lido" },
  { symbol: "ARB/USDT", name: "Arbitrum" },
  { symbol: "OP/USDT", name: "Optimism" },
  { symbol: "STX/USDT", name: "Stacks" },
  { symbol: "RUNE/USDT", name: "THORChain" },
  { symbol: "GRT/USDT", name: "The Graph" },
  { symbol: "IMX/USDT", name: "Immutable" },
  { symbol: "SAND/USDT", name: "The Sandbox" },
  { symbol: "MANA/USDT", name: "Decentraland" },
  { symbol: "PEPE/USDT", name: "Pepe" },
  { symbol: "SHIB/USDT", name: "Shiba Inu" },
  { symbol: "BONK/USDT", name: "Bonk" },
  { symbol: "WIF/USDT", name: "dogwifhat" },
  { symbol: "FLOKI/USDT", name: "Floki" },
  { symbol: "JUP/USDT", name: "Jupiter" },
  { symbol: "PYTH/USDT", name: "Pyth" },
  { symbol: "ONDO/USDT", name: "Ondo" },
  { symbol: "ENA/USDT", name: "Ethena" },
  { symbol: "TAO/USDT", name: "Bittensor" },
  { symbol: "PENDLE/USDT", name: "Pendle" },
  { symbol: "CRV/USDT", name: "Curve" },
  { symbol: "POL/USDT", name: "Polygon" },
  { symbol: "ALGO/USDT", name: "Algorand" },
  { symbol: "XLM/USDT", name: "Stellar" },
  { symbol: "KAS/USDT", name: "Kaspa" },
  { symbol: "ORDI/USDT", name: "ORDI" },
  { symbol: "WLD/USDT", name: "Worldcoin" },
  { symbol: "FET/USDT", name: "Artificial Superintelligence" },
];

export const COIN_SYMBOLS = MARKETS.map((market) => market.symbol);

const NAMES = new Map(MARKETS.map((market) => [market.symbol, market.name]));

export function marketName(symbol: string): string {
  return NAMES.get(symbol) ?? symbol.split("/")[0] ?? symbol;
}

export function marketAliases(): Record<string, string> {
  const aliases: Record<string, string> = {};
  for (const market of MARKETS) {
    aliases[market.name.toLowerCase()] = market.symbol.split("/")[0] ?? market.symbol;
  }
  return aliases;
}
