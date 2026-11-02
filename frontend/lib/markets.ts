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

/** Logo image for a coin ticker, when one is known. */
const COIN_LOGOS: Record<string, string> = {
  BTC: "https://cryptologos.cc/logos/bitcoin-btc-logo.png",
  ETH: "https://cryptologos.cc/logos/ethereum-eth-logo.png",
  SOL: "https://cryptologos.cc/logos/solana-sol-logo.png",
  BNB: "https://cryptologos.cc/logos/bnb-bnb-logo.png",
  XRP: "https://cryptologos.cc/logos/xrp-xrp-logo.png",
  DOGE: "https://cryptologos.cc/logos/dogecoin-doge-logo.png",
  ADA: "https://cryptologos.cc/logos/cardano-ada-logo.png",
  AVAX: "https://cryptologos.cc/logos/avalanche-avax-logo.png",
  LINK: "https://cryptologos.cc/logos/chainlink-link-logo.png",
  DOT: "https://cryptologos.cc/logos/polkadot-new-dot-logo.png",
  TRX: "https://cryptologos.cc/logos/tron-trx-logo.png",
  TON: "https://cryptologos.cc/logos/toncoin-ton-logo.png",
  LTC: "https://cryptologos.cc/logos/litecoin-ltc-logo.png",
  BCH: "https://cryptologos.cc/logos/bitcoin-cash-bch-logo.png",
  ATOM: "https://cryptologos.cc/logos/cosmos-atom-logo.png",
  NEAR: "https://cryptologos.cc/logos/near-protocol-near-logo.png",
  APT: "https://cryptologos.cc/logos/aptos-apt-logo.png",
  SUI: "https://cryptologos.cc/logos/sui-sui-logo.png",
  SEI: "https://cryptologos.cc/logos/sei-sei-logo.png",
  INJ: "https://cryptologos.cc/logos/injective-inj-logo.png",
  TIA: "https://cryptologos.cc/logos/celestia-tia-logo.png",
  FIL: "https://cryptologos.cc/logos/filecoin-fil-logo.png",
  ICP: "https://cryptologos.cc/logos/internet-computer-icp-logo.png",
  HBAR: "https://cryptologos.cc/logos/hedera-hbar-logo.png",
  ETC: "https://cryptologos.cc/logos/ethereum-classic-etc-logo.png",
  UNI: "https://cryptologos.cc/logos/uniswap-uni-logo.png",
  AAVE: "https://cryptologos.cc/logos/aave-aave-logo.png",
  MKR: "https://cryptologos.cc/logos/maker-mkr-logo.png",
  LDO: "https://cryptologos.cc/logos/lido-dao-ldo-logo.png",
  ARB: "https://cryptologos.cc/logos/arbitrum-arb-logo.png",
  OP: "https://cryptologos.cc/logos/optimism-ethereum-op-logo.png",
  STX: "https://cryptologos.cc/logos/stacks-stx-logo.png",
  RUNE: "https://cryptologos.cc/logos/thorchain-rune-logo.png",
  GRT: "https://cryptologos.cc/logos/the-graph-grt-logo.png",
  IMX: "https://cryptologos.cc/logos/immutable-x-imx-logo.png",
  SAND: "https://cryptologos.cc/logos/the-sandbox-sand-logo.png",
  MANA: "https://cryptologos.cc/logos/decentraland-mana-logo.png",
  PEPE: "https://cryptologos.cc/logos/pepe-pepe-logo.png",
  SHIB: "https://cryptologos.cc/logos/shiba-inu-shib-logo.png",
  BONK: "https://cryptologos.cc/logos/bonk1-bonk-logo.png",
  WIF: "https://cryptologos.cc/logos/dogwifhat-wif-logo.png",
  FLOKI: "https://cryptologos.cc/logos/floki-inu-floki-logo.png",
  JUP: "https://cryptologos.cc/logos/jupiter-jup-logo.png",
  PYTH: "https://cryptologos.cc/logos/pyth-network-pyth-logo.png",
  ONDO: "https://cryptologos.cc/logos/ondo-ondo-logo.png",
  ENA: "https://cryptologos.cc/logos/ethena-ena-logo.png",
  TAO: "https://cryptologos.cc/logos/bittensor-tao-logo.png",
  PENDLE: "https://cryptologos.cc/logos/pendle-pendle-logo.png",
  CRV: "https://cryptologos.cc/logos/curve-dao-token-crv-logo.png",
  POL: "https://cryptologos.cc/logos/polygon-matic-logo.png",
  ALGO: "https://cryptologos.cc/logos/algorand-algo-logo.png",
  XLM: "https://cryptologos.cc/logos/stellar-xlm-logo.png",
  KAS: "https://cryptologos.cc/logos/kaspa-kas-logo.png",
  ORDI: "https://cryptologos.cc/logos/ordi-ordi-logo.png",
  WLD: "https://coin-images.coingecko.com/coins/images/31069/small/worldcoin.jpeg?1696529903",
  FET: "https://coin-images.coingecko.com/coins/images/5681/small/ASI.png?1719827289",
  USDT: "https://cryptologos.cc/logos/tether-usdt-logo.png",
  USDC: "https://cryptologos.cc/logos/usd-coin-usdc-logo.png",
};

export function coinLogo(ticker: string): string | undefined {
  return COIN_LOGOS[ticker.toUpperCase()];
}
