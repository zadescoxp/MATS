import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "#09090b",
        surface: "#0f0f12",
        card: "#141418",
        "card-hover": "#1a1a1f",
        border: "#27272a",
        foreground: "#fafafa",
        muted: "#a1a1aa",
        "muted-foreground": "#71717a",
        accent: "#3b82f6",
        positive: "#22c55e",
        negative: "#ef4444",
      },
      borderRadius: {
        xl: "0.75rem",
        "2xl": "0.75rem",
      },
    },
  },
  plugins: [],
};

export default config;
