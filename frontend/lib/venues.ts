/** Trading venues MATS can read balances from. Bybit is the only one today. */
export interface Venue {
  id: string;
  name: string;
  logo: string;
  website: string;
}

export const VENUES: Record<string, Venue> = {
  bybit: {
    id: "bybit",
    name: "Bybit",
    logo: "https://coin-images.coingecko.com/markets/images/698/large/bybit_spot.png",
    website: "https://www.bybit.com",
  },
};

export function venueFor(id: string | undefined): Venue | undefined {
  return id ? VENUES[id] : undefined;
}
