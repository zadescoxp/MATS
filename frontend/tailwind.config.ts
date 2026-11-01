import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "#0a0a0b",
        surface: "#111113",
        card: "#161618",
        "card-hover": "#1c1c1f",
        border: "#242427",
        foreground: "#ededef",
        muted: "#a1a1a9",
        "muted-foreground": "#6e6e77",
        // A calm indigo used only for state and selection, never decoration.
        accent: "#6b78e5",
        positive: "#4cc38a",
        negative: "#e5686b",
      },
      borderRadius: {
        xl: "0.625rem",
        "2xl": "0.875rem",
        "3xl": "1.125rem",
      },
      letterSpacing: {
        tightish: "-0.011em",
      },
    },
  },
  plugins: [],
};

export default config;
