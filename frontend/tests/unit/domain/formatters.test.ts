import { describe, expect, it } from "vitest";
import { formatPercentage, formatShortUuid } from "../../../src/domain/shared/formatters";

describe("Domain Shared Formatters", () => {
  it("normalizes [0, 1] ratios and (1, 100] scores cleanly without double-multiplication", () => {
    expect(formatPercentage(0)).toBe("0.0%");
    expect(formatPercentage(-5)).toBe("0.0%");
    expect(formatPercentage(Number.NaN)).toBe("0.0%");
    expect(formatPercentage(0.914)).toBe("91.4%");
    expect(formatPercentage(1.0)).toBe("100.0%");
    expect(formatPercentage(45.317)).toBe("45.3%");
    expect(formatPercentage(89.64, 1)).toBe("89.6%");
    expect(formatPercentage(105.2)).toBe("100.0%");
  });

  it("truncates long UUIDs cleanly for KPI headers", () => {
    expect(formatShortUuid("01a1ce86-9f22-7c00-8000-abcdef125036")).toBe("01a1ce86…5036");
    expect(formatShortUuid("sess-demo-01")).toBe("sess-demo-01");
  });
});
