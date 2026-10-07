import { Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";
import Nav from "@/components/Nav";
import { getRuns } from "@/lib/data";
import "./globals.css";

// Every page reads the database and the ?week= param: always render at request time.
export const dynamic = "force-dynamic";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata = {
  title: { default: "Supertrend Sector Scanner", template: "%s · Supertrend Scanner" },
  description: "Weekly Supertrend sector-rotation scanner for the Nifty Total Market (Nifty 500 + Microcap 250)",
};

// Dark by default; the stored choice is applied before first paint (no flash).
const themeScript = `try{var t=localStorage.getItem('theme');document.documentElement.classList.toggle('dark',t!=='light')}catch(e){document.documentElement.classList.add('dark')}`;

export default async function RootLayout({ children }) {
  const runs = await getRuns({ completeOnly: true });
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} dark h-full antialiased`}
      suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-full flex flex-col">
        <Suspense fallback={<div className="h-14 border-b border-line" />}>
          <Nav weeks={runs.map((r) => r.week_end_date)} />
        </Suspense>
        <main className="flex-1 w-full max-w-[1600px] mx-auto px-4 py-5">{children}</main>
      </body>
    </html>
  );
}
