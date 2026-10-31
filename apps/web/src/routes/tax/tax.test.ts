import { describe, expect, it } from "vitest";

import { resolveCode, splitFils } from "@/routes/tax/tax";

describe("line VAT by price mode (as tax.split)", () => {
  it("takes VAT out of inclusive amounts like the API's own examples", () => {
    expect(splitFils(3150, "5", "vat_inclusive")).toEqual([3000, 150]);
    expect(splitFils(7, "5", "vat_inclusive")).toEqual([7, 0]);
    expect(splitFils(1393, "5", "vat_inclusive")).toEqual([1327, 66]);
  });

  it("adds VAT to exclusive amounts, half-up to the fils", () => {
    expect(splitFils(12000, "5", "vat_exclusive")).toEqual([12000, 600]);
    expect(splitFils(10, "5", "vat_exclusive")).toEqual([10, 1]); // 0.5 fils rounds up
    expect(splitFils(9, "5", "vat_exclusive")).toEqual([9, 0]);
  });

  it("charges nothing with no VAT or a zero rate", () => {
    expect(splitFils(12000, "5", "no_vat")).toEqual([12000, 0]);
    expect(splitFils(12000, "0", "vat_inclusive")).toEqual([12000, 0]);
  });

  it("inclusive: net + VAT is what was typed, and VAT is amount × 5/105 rounded half-up, for every amount", () => {
    for (let amount = 0; amount <= 30000; amount++) {
      const [net, vat] = splitFils(amount, "5", "vat_inclusive");
      expect(net + vat).toBe(amount);
      // vat is the nearest whole fils to 5a/105, ties up: |105·vat − 5a| ≤ 52.5, and on a tie it is above.
      const gap = 105 * vat - 5 * amount;
      expect(2 * Math.abs(gap)).toBeLessThanOrEqual(105);
      if (2 * Math.abs(gap) === 105) expect(gap).toBeGreaterThan(0);
    }
  });

  it("exclusive: VAT is amount × 5/100 rounded half-up, for every amount", () => {
    for (let amount = 0; amount <= 30000; amount++) {
      const [net, vat] = splitFils(amount, "5", "vat_exclusive");
      expect(net).toBe(amount);
      const gap = 100 * vat - 5 * amount;
      expect(2 * Math.abs(gap)).toBeLessThanOrEqual(100);
      if (2 * Math.abs(gap) === 100) expect(gap).toBeGreaterThan(0);
    }
  });
});

describe("a line's default code (as tax.resolve)", () => {
  const base = { side: "sales" as const, registered: true, mode: "vat_exclusive" as const, reason: null };

  it("takes the chosen code, then the line followed, the party's, the item's, then standard", () => {
    const all = { typed: "EX", followed: "ZR", party: "OS", product: "EX" };
    expect(resolveCode({ ...base, ...all })).toBe("EX");
    expect(resolveCode({ ...base, ...all, typed: "" })).toBe("ZR");
    expect(resolveCode({ ...base, ...all, typed: "", followed: null })).toBe("OS");
    expect(resolveCode({ ...base, typed: "", followed: null, party: null, product: "EX" })).toBe("EX");
    expect(resolveCode(base)).toBe("SR");
  });

  it("lets the no-VAT mode decide every line by its reason", () => {
    expect(resolveCode({ ...base, mode: "no_vat", reason: "exempt", typed: "SR" })).toBe("EX");
    expect(resolveCode({ ...base, mode: "no_vat", reason: "zero_rated" })).toBe("ZR");
  });

  it("charges nothing for a sales business that is not registered, but purchases still record VAT", () => {
    expect(resolveCode({ ...base, registered: false, typed: "SR" })).toBe("OS");
    expect(resolveCode({ ...base, side: "purchase", registered: false })).toBe("SR");
  });
});
