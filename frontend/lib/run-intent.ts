const BASES = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT"];

const ALIASES: Record<string, string> = {
  bitcoin: "BTC",
  ethereum: "ETH",
  solana: "SOL",
  ripple: "XRP",
  cardano: "ADA",
  dogecoin: "DOGE",
};

const TIMEFRAME_PATTERNS: Array<[RegExp, string]> = [
  [/\b15\s*(?:m|min|mins|minute|minutes)\b/gi, "15m"],
  [/\b30\s*(?:m|min|mins|minute|minutes)\b/gi, "30m"],
  [/\b12\s*(?:h|hr|hrs|hour|hours)\b/gi, "12h"],
  [/\b6\s*(?:h|hr|hrs|hour|hours)\b/gi, "6h"],
  [/\b4\s*(?:h|hr|hrs|hour|hours)\b/gi, "4h"],
  [/\b2\s*(?:h|hr|hrs|hour|hours)\b/gi, "2h"],
  [/\b1\s*(?:h|hr|hrs|hour|hours)\b/gi, "1h"],
  [/\b5\s*(?:m|min|mins|minute|minutes)\b/gi, "5m"],
  [/\b1\s*(?:m|min|mins|minute|minutes)\b/gi, "1m"],
  [/\b(?:daily|1\s*d|1\s*day|1\s*days)\b/gi, "1d"],
  [/\b(?:weekly|1\s*w|1\s*week|1\s*weeks)\b/gi, "1w"],
];

export interface ParsedRunRequest {
  symbols: string[];
  timeframes: string[];
  equity: number | null;
  equitySpecified: boolean;
  dryRun: boolean | null;
}

function addSymbol(found: string[], base: string, quote = "USDT") {
  const normalizedQuote = quote === "USD" || quote === "USDC" ? "USDT" : quote;
  const symbol = `${base.toUpperCase()}/${normalizedQuote.toUpperCase()}`;
  if (!found.includes(symbol)) found.push(symbol);
}

function readSymbols(text: string): string[] {
  const found: string[] = [];
  let masked = text;
  for (const match of text.matchAll(/\b([A-Za-z]{2,10})\s*\/\s*(USDT|USD|USDC)\b/gi)) {
    addSymbol(found, match[1], match[2]);
    masked = masked.replace(match[0], " ");
  }
  for (const [alias, base] of Object.entries(ALIASES)) {
    if (new RegExp(`\\b${alias}\\b`, "i").test(masked)) addSymbol(found, base);
  }
  for (const base of BASES) {
    if (new RegExp(`\\b${base}\\b`, "i").test(masked)) addSymbol(found, base);
  }
  return found;
}

function readTimeframes(text: string): string[] {
  const found: string[] = [];
  for (const [pattern, label] of TIMEFRAME_PATTERNS) {
    if (pattern.test(text) && !found.includes(label)) found.push(label);
    pattern.lastIndex = 0;
  }
  return found;
}

function readEquity(text: string): { value: number | null; specified: boolean } {
  if (/\b(available balance|skip capital|no equity|without capital)\b/i.test(text)) {
    return { value: null, specified: true };
  }
  const patterns = [
    /(?:capital|equity|account|bankroll)\s*(?:of\s*)?\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?/i,
    /\$\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?/,
    /(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(?:usdt|usd)\b/i,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (!match) continue;
    let amount = Number(match[1].replaceAll(",", ""));
    if (match[2]) amount *= 1000;
    if (Number.isFinite(amount) && amount > 0) return { value: amount, specified: true };
  }
  return { value: null, specified: false };
}

export function parseRunRequest(input: string): ParsedRunRequest {
  const text = input.trim();
  const equity = readEquity(text);
  let dryRun: boolean | null = null;
  if (/\b(dry[\s-]?run|paper)\b/i.test(text)) dryRun = true;
  else if (/\blive\b/i.test(text)) dryRun = false;
  return {
    symbols: readSymbols(text),
    timeframes: readTimeframes(text),
    equity: equity.value,
    equitySpecified: equity.specified,
    dryRun,
  };
}
