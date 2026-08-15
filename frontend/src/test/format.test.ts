import { describe, expect, it } from "vitest";

import {
  cn,
  formatDistance,
  formatPercent,
  formatPrice,
  formatPriceRange,
  initials,
  scoreBand,
  sentimentLabel,
  truncate,
} from "@/lib/format";

describe("formatPrice", () => {
  it("formats rupees without decimals", () => {
    expect(formatPrice(120)).toContain("120");
  });

  it("renders an em dash for missing values rather than ₹0", () => {
    expect(formatPrice(null)).toBe("—");
    expect(formatPrice(undefined)).toBe("—");
  });
});

describe("formatPriceRange", () => {
  it("collapses an identical min and max to one value", () => {
    expect(formatPriceRange(120, 120)).not.toContain("–");
  });

  it("renders a range when they differ", () => {
    expect(formatPriceRange(60, 220)).toContain("–");
  });

  it("handles a single-sided range", () => {
    expect(formatPriceRange(null, 200)).toContain("200");
  });
});

describe("formatDistance", () => {
  it("uses metres below a kilometre", () => {
    expect(formatDistance(740)).toBe("740 m");
  });

  it("uses kilometres above that", () => {
    expect(formatDistance(4200)).toBe("4.2 km");
  });

  it("returns empty for unknown distance", () => {
    expect(formatDistance(null)).toBe("");
  });
});

describe("scoreBand", () => {
  it("maps scores to bands", () => {
    expect(scoreBand(92)).toBe("excellent");
    expect(scoreBand(70)).toBe("good");
    expect(scoreBand(50)).toBe("mixed");
    expect(scoreBand(20)).toBe("poor");
  });

  it("treats a missing score as unknown, not poor", () => {
    expect(scoreBand(null)).toBe("unknown");
    expect(scoreBand(undefined)).toBe("unknown");
  });
});

describe("sentimentLabel", () => {
  it("uses a neutral band around zero", () => {
    expect(sentimentLabel(0.05)).toBe("neutral");
    expect(sentimentLabel(0.9)).toBe("positive");
    expect(sentimentLabel(-0.9)).toBe("negative");
  });
});

describe("formatPercent", () => {
  it("rounds to whole percentages", () => {
    expect(formatPercent(0.914)).toBe("91%");
  });

  it("renders an em dash when unknown", () => {
    expect(formatPercent(null)).toBe("—");
  });
});

describe("misc helpers", () => {
  it("joins class names and drops falsy values", () => {
    expect(cn("a", false, undefined, "b")).toBe("a b");
  });

  it("builds initials from a name", () => {
    expect(initials("Rahul Das")).toBe("RD");
    expect(initials("momo")).toBe("M");
  });

  it("truncates with an ellipsis", () => {
    expect(truncate("abcdefghij", 5)).toBe("abcd…");
    expect(truncate("abc", 5)).toBe("abc");
  });
});
