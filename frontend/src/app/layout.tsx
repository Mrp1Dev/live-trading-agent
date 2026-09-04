import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Alpaca Autonomous Options Agent",
  description: "Material Design 3 Autonomous Options Trading Pipeline Dashboard for Alpaca Paper Trading",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" data-theme="light">
      <body>{children}</body>
    </html>
  );
}
