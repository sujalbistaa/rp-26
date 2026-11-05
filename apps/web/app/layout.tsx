import type { Metadata } from "next";
import { cookies } from "next/headers";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Rourkela Police / Face Match",
  description: "Internal. Authorised officers only.",
  robots: { index: false, follow: false },
  icons: { icon: [] },
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const theme = (await cookies()).get("rkl_theme")?.value === "night" ? "night" : "day";
  return (
    <html lang="en" data-theme={theme}>
      <body>{children}</body>
    </html>
  );
}
