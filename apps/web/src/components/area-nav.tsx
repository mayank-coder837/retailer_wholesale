import { Link, useRouterState } from "@tanstack/react-router";

import { cn } from "@/lib/utils";

export type AreaLink = {
  to: string;
  label: string;
  /** Whether this entry is the current section; deeper pages (a voucher, a product) belong to one. */
  matches: (path: string) => boolean;
};

/** An area's own menu under its title (Accounts, Inventory): pills, the current section highlighted. */
export function AreaNav({ label, links }: { label: string; links: AreaLink[] }) {
  const path = useRouterState({ select: (s) => s.location.pathname });
  return (
    <nav aria-label={label} className="flex flex-wrap gap-1 border-b pb-2 text-sm">
      {links.map((l) => {
        const current = l.matches(path);
        return (
          <Link
            key={l.to}
            to={l.to}
            className={cn("rounded-md px-3 py-1.5 hover:bg-muted", current && "bg-secondary font-semibold")}
            aria-current={current ? "page" : undefined}
          >
            {l.label}
          </Link>
        );
      })}
    </nav>
  );
}
