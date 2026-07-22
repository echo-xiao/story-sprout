import type { Metadata } from "next";
import "./globals.css";
import AccessGate from "@/components/AccessGate";

export const metadata: Metadata = {
  title: "StorySprout",
  description: "Transform any book into a beautiful children's picture book with AI",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <AccessGate>{children}</AccessGate>
      </body>
    </html>
  );
}
