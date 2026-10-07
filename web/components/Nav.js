"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

const LINKS = [
  ["/", "Dashboard"],
  ["/sectors", "Sectors"],
  ["/scanner", "Scanner"],
  ["/runs", "Runs"],
  ["/backtests", "Backtests"],
  ["/backtest", "Sweeps"],
];

export default function Nav({ weeks }) {
  const pathname = usePathname();
  const router = useRouter();
  const sp = useSearchParams();
  const week = sp.get("week") ?? weeks[0] ?? "";

  // Keep the selected week when moving between pages.
  const withWeek = (href) => (sp.get("week") ? `${href}?week=${sp.get("week")}` : href);

  function onWeek(e) {
    const next = new URLSearchParams(sp);
    if (e.target.value === weeks[0]) next.delete("week");
    else next.set("week", e.target.value);
    const qs = next.toString();
    router.push(qs ? `${pathname}?${qs}` : pathname);
  }

  function toggleTheme() {
    const dark = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", dark);
    try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch {}
  }

  return (
    <header className="border-b border-line bg-panel/80 backdrop-blur sticky top-0 z-30">
      <div className="max-w-[1600px] mx-auto px-4 py-2 sm:py-0 sm:h-14 flex flex-wrap sm:flex-nowrap items-center gap-x-6 gap-y-2">
        <Link href={withWeek("/")} className="font-semibold tracking-tight whitespace-nowrap">
          Supertrend<span className="text-accent"> Rotation</span>
        </Link>
        <nav className="order-last sm:order-none w-full sm:w-auto flex gap-1 text-sm overflow-x-auto">
          {LINKS.map(([href, label]) => {
            const active = href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link key={href} href={withWeek(href)}
                className={`px-3 py-1.5 rounded-md whitespace-nowrap ${active ? "bg-accent-bg text-accent" : "text-muted hover:text-text"}`}>
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <label className="text-xs text-muted hidden sm:block" htmlFor="week">Week</label>
          <select id="week" className="input tabular max-w-[150px] sm:max-w-none" value={week} onChange={onWeek} disabled={!weeks.length}>
            {weeks.map((w, i) => (
              <option key={w} value={w}>{w}{i === 0 ? " (latest)" : ""}</option>
            ))}
          </select>
          <button className="btn" onClick={toggleTheme} aria-label="Toggle dark mode" title="Toggle theme">◐</button>
        </div>
      </div>
    </header>
  );
}
