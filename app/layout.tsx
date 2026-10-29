import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "MATS | Research Console",
  description: "Local live console for the MATS trading research framework.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
