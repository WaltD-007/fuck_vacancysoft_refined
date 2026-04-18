import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Prospero",
  description: "Recruitment Intelligence Platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased" suppressHydrationWarning>
      <head>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
      </head>
      <body className="min-h-full" style={{ fontFamily: "'Inter', sans-serif" }}>{children}</body>
    </html>
  );
}
