import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./components/Providers";

export const metadata: Metadata = {
  title: "DataDive — Agent Swarm Dashboard",
  description: "Live spectator view of the DataDive multi-agent swarm",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full bg-gray-950 text-gray-100 font-mono">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
