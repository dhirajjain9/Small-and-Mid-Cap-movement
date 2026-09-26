import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NSE Screener",
  description: "Every NSE stock: price moves, volume, delivery, circuit bands and signals. Personal use only.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
