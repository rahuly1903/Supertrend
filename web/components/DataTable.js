"use client";

import {
  flexRender, getCoreRowModel, getSortedRowModel, useReactTable,
} from "@tanstack/react-table";
import Link from "next/link";
import { useMemo, useState } from "react";
import { date, num, pct } from "@/lib/format";
import { Badge, DirectionBadge, GradeBadge, QuadrantBadge } from "./ui";

// Column spec (serialisable, so server components can pass it):
// { key, header, type: text|num|int|pct|date|bool|symbol|sector|direction|grade|quadrant|reasons, d, unsigned, tone }
function renderCell(col, row, week) {
  const v = row[col.key];
  const q = week ? `?week=${week}` : "";
  switch (col.type) {
    case "symbol":
      return <Link href={`/stock/${encodeURIComponent(v)}${q}`} className="font-medium text-accent hover:underline">{v}</Link>;
    case "sector":
      return <Link href={`/sectors/${encodeURIComponent(v)}${q}`} className="hover:underline">{v}</Link>;
    case "direction":
      return <DirectionBadge direction={v} isNew={row.is_new_flip} />;
    case "grade":
      return <GradeBadge grade={v} />;
    case "quadrant":
      return <QuadrantBadge q={v} />;
    case "bool":
      return v == null ? <span className="text-muted">—</span> : v ? <Badge tone="up">Yes</Badge> : <Badge>No</Badge>;
    case "pct": {
      const tone = col.unsigned || v == null || v === 0 ? "" : v > 0 ? "text-up" : "text-down";
      return <span className={tone}>{pct(v, col.d ?? 1, !col.unsigned, col.suffix ?? "%")}</span>;
    }
    case "num": {
      const tone = col.tone && v != null ? (v > col.tone ? "text-up" : "text-down") : "";
      return <span className={tone}>{num(v, col.d ?? 2)}</span>;
    }
    case "int":
      return num(v, 0);
    case "date":
      return date(v);
    case "link":
      return <Link href={col.href.replace(/\{(\w+)\}/g, (_, k) => row[k] ?? "")} className="text-accent hover:underline">{v}</Link>;
    case "reasons":
      return <span className="text-xs text-muted">{v}</span>;
    case "index":
      return <span className="text-xs text-muted">{(v ?? "").split("|").join(" · ")}</span>;
    default:
      return v ?? <span className="text-muted">—</span>;
  }
}

const NUMERIC = new Set(["num", "int", "pct"]);

export default function DataTable({
  rows, columns, week, initialSort = [], maxHeight = "70vh", rowKey = "symbol", onSortChange, sorting: controlled,
}) {
  const [internal, setInternal] = useState(initialSort);
  const sorting = controlled ?? internal;
  const setSorting = (updater) => {
    const next = typeof updater === "function" ? updater(sorting) : updater;
    if (onSortChange) onSortChange(next);
    else setInternal(next);
  };

  const colDefs = useMemo(() => columns.map((c) => ({
    id: c.key,
    accessorFn: (r) => r[c.key],
    header: c.header,
    meta: c,
    sortingFn: NUMERIC.has(c.type) ? "basic" : "alphanumeric",
    sortUndefined: "last",
    cell: ({ row }) => renderCell(c, row.original, week),
  })), [columns, week]);

  const table = useReactTable({
    data: rows, columns: colDefs, state: { sorting }, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
    getRowId: (r, i) => String(r[rowKey] ?? i),
  });

  return (
    <div className="overflow-auto border border-line rounded-lg" style={{ maxHeight }}>
      <table className="w-full text-[13px] tabular border-separate border-spacing-0">
        <thead className="sticky top-0 z-10">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h, i) => {
                const meta = h.column.columnDef.meta;
                const sorted = h.column.getIsSorted();
                return (
                  <th key={h.id} onClick={h.column.getToggleSortingHandler()}
                    className={`bg-panel-2 border-b border-line px-2.5 py-2 text-xs font-medium text-muted whitespace-nowrap select-none cursor-pointer hover:text-text
                      ${NUMERIC.has(meta.type) ? "text-right" : "text-left"} ${i === 0 ? "sticky left-0 z-20" : ""}`}>
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    <span className="ml-1 text-accent">{sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : ""}</span>
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className="group">
              {row.getVisibleCells().map((cell, i) => {
                const meta = cell.column.columnDef.meta;
                return (
                  <td key={cell.id}
                    className={`border-b border-line px-2.5 py-1.5 group-hover:bg-panel-2
                      ${NUMERIC.has(meta.type) ? "text-right whitespace-nowrap" : ""}
                      ${meta.type === "reasons" ? "min-w-[300px]" : meta.type === "index" ? "min-w-[220px]" : "whitespace-nowrap"}
                      ${i === 0 ? "sticky left-0 bg-panel z-[1]" : ""}`}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                );
              })}
            </tr>
          ))}
          {rows.length === 0 ? (
            <tr><td colSpan={columns.length} className="text-center text-muted py-8">No rows match.</td></tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
