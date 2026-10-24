import type { ReactNode } from "react";

/** A code (account code, SKU) before a name. The code reads left to right; the gap after it follows the page,
 * so in Arabic it falls between the code and the name too. */
export function CodeTag({ children }: { children: ReactNode }) {
  return (
    <span className="me-2">
      <span className="font-mono text-muted-foreground" dir="ltr">
        {children}
      </span>
    </span>
  );
}
