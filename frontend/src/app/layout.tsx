import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Picture Book Generator",
  description: "Transform any book into a beautiful children's picture book with AI",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
