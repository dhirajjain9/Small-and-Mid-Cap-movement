import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "India Stock Screener",
  description: "Every listed NSE and BSE company: price moves, volume, delivery, circuit bands and signals. Personal use only.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
