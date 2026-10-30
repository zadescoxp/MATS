import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "#0c0c0e",
        surface: "#141416",
        card: "#1a1a1e",
        "card-hover": "#222226",
        border: "#2a2a2e",
        foreground: "#f5f5f6",
        muted: "#a1a1aa",
        "muted-foreground": "#71717a",
        accent: "#3b82f6",
        positive: "#34d399",
        negative: "#f87171",
      },
      borderRadius: {
        xl: "0.9rem",
        "2xl": "1.25rem",
        "3xl": "1.75rem",
      },
    },
  },
  plugins: [],
};

export default config;
