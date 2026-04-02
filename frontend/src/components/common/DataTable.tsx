import type { ReactNode } from "react";

interface DataTableProps {
  headers: string[];
  children: ReactNode;
  className?: string;
}

export function DataTable({ headers, children, className }: DataTableProps) {
  return (
    <table
      className={`min-w-full text-left text-xs text-slate-200 ${className ?? ""}`}
    >
      <thead className="border-b border-border/80 bg-slate-950/80 text-[11px] uppercase tracking-wide text-slate-400">
        <tr>
          {headers.map((header) => (
            <th key={header} className="px-3 py-2 font-medium">
              {header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
}
