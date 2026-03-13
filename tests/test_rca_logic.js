/**
 * tests/test_rca_logic.js
 * ========================
 * Vitest unit tests for the frontend duplicate-check logic extracted from
 * autorca_dashboard.html.
 *
 * We isolate the pure logic functions so they can be tested without a DOM.
 *
 * Scenarios covered
 * -----------------
 * 1.  No records returned from API  → should NOT flag as duplicate
 * 2.  Matching record in API response → should flag as duplicate
 * 3.  Partial match (same source, different totals) → NOT a duplicate
 * 4.  Partial match (same totals, different source) → NOT a duplicate
 * 5.  Multiple records, one matches → IS a duplicate
 * 6.  API returns ok:false (error) → should NOT block save (fail open)
 * 7.  Network error during check → should NOT block save (fail open)
 * 8.  After deletion, re-check returns empty → NOT a duplicate
 * 9.  Save payload builder produces correct field types
 * 10. Severity normalisation (error→critical, healthy→healthy, else→warning)
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

// ---------------------------------------------------------------------------
// Pure logic extracted from autorca_dashboard.html
// (In your build pipeline you would import these from a shared module;
//  here we re-define them so the tests run without a DOM / bundler.)
// ---------------------------------------------------------------------------

/**
 * Given an API response and the current RCA stats, decide whether the record
 * already exists in the database.
 *
 * Returns: { isDuplicate: boolean, matchedRecord: object|null }
 */
function checkForDuplicate(apiData, sourceName, totalEntries, errorCount) {
  if (!apiData?.ok || !Array.isArray(apiData.data)) {
    // API error — fail open (allow save)
    return { isDuplicate: false, matchedRecord: null };
  }

  const match = apiData.data.find(
    (r) =>
      r.source_name === sourceName &&
      (r.total_entries || 0) === totalEntries &&
      (r.error_count || 0) === errorCount
  );

  return {
    isDuplicate: Boolean(match),
    matchedRecord: match ?? null,
  };
}

/**
 * Normalise severity string from RCA engine output to DB enum value.
 */
function normaliseSeverity(rcaClass) {
  if (rcaClass === "error") return "critical";
  if (rcaClass === "healthy") return "healthy";
  return "warning";
}

/**
 * Build the save payload from RCA state.
 */
function buildPayload({ label, stats, rcaClass, aiSummary, fixText }) {
  const s = stats || {};
  return {
    source_name:   label || "Unknown Source",
    severity:      normaliseSeverity(rcaClass || "warning"),
    total_entries: parseInt(s.total ?? 0, 10),
    error_count:   parseInt(s.err ?? 0, 10),
    warn_count:    parseInt(s.warn ?? 0, 10),
    error_rate:    parseFloat(s.rate ?? 0),
    ai_summary:    aiSummary || "",
    fix_steps:     fixText || "",
  };
}

/**
 * Simulate the full save flow:
 *   1. Call checkApi (mocked fetch to GET /api/rca/history?search=...)
 *   2. If duplicate found → return { action: "show_modal", record }
 *   3. If no duplicate → return { action: "save" }
 *   4. If API error → return { action: "save" }  (fail open)
 */
async function saveFlowDecision(fetchFn, sourceName, totalEntries, errorCount) {
  try {
    const resp = await fetchFn(
      `/api/rca/history?search=${encodeURIComponent(sourceName)}&limit=50`
    );
    const data = await resp.json();
    const { isDuplicate, matchedRecord } = checkForDuplicate(
      data,
      sourceName,
      totalEntries,
      errorCount
    );
    if (isDuplicate) {
      return { action: "show_modal", record: matchedRecord };
    }
    return { action: "save" };
  } catch {
    // Network error → fail open
    return { action: "save" };
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeApiResponse(records) {
  return {
    ok: async () => ({ ok: true, data: records }),
    json: async () => ({ ok: true, data: records }),
  };
}

function makeApiErrorResponse() {
  return {
    json: async () => ({ ok: false, error: "Supabase error" }),
  };
}

function makeRecord(overrides = {}) {
  return {
    id:            "rec-abc-123",
    source_name:   "app.log",
    total_entries: 2500,
    error_count:   1310,
    severity:      "critical",
    created_at:    "2026-03-13T07:00:00+00:00",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("checkForDuplicate", () => {
  it("returns isDuplicate:false when data array is empty", () => {
    const result = checkForDuplicate({ ok: true, data: [] }, "app.log", 2500, 1310);
    expect(result.isDuplicate).toBe(false);
    expect(result.matchedRecord).toBeNull();
  });

  it("detects a matching record", () => {
    const record = makeRecord();
    const result = checkForDuplicate(
      { ok: true, data: [record] },
      "app.log",
      2500,
      1310
    );
    expect(result.isDuplicate).toBe(true);
    expect(result.matchedRecord).toEqual(record);
  });

  it("does NOT flag as duplicate when totals differ", () => {
    const record = makeRecord({ total_entries: 9999, error_count: 999 });
    const result = checkForDuplicate(
      { ok: true, data: [record] },
      "app.log",
      2500,    // different
      1310
    );
    expect(result.isDuplicate).toBe(false);
  });

  it("does NOT flag as duplicate when source name differs", () => {
    const record = makeRecord({ source_name: "other.log" });
    const result = checkForDuplicate(
      { ok: true, data: [record] },
      "app.log",  // different
      2500,
      1310
    );
    expect(result.isDuplicate).toBe(false);
  });

  it("finds the matching record among multiple records", () => {
    const records = [
      makeRecord({ id: "r1", source_name: "other.log" }),
      makeRecord({ id: "r2" }),   // <-- this one matches
      makeRecord({ id: "r3", total_entries: 100 }),
    ];
    const result = checkForDuplicate({ ok: true, data: records }, "app.log", 2500, 1310);
    expect(result.isDuplicate).toBe(true);
    expect(result.matchedRecord.id).toBe("r2");
  });

  it("fails open when API returns ok:false", () => {
    const result = checkForDuplicate({ ok: false, error: "DB error" }, "app.log", 2500, 1310);
    expect(result.isDuplicate).toBe(false);
  });

  it("fails open when apiData is null", () => {
    const result = checkForDuplicate(null, "app.log", 2500, 1310);
    expect(result.isDuplicate).toBe(false);
  });

  it("fails open when data is not an array", () => {
    const result = checkForDuplicate({ ok: true, data: null }, "app.log", 2500, 1310);
    expect(result.isDuplicate).toBe(false);
  });

  it("after deletion empty response is NOT a duplicate", () => {
    // Simulates: record was deleted → API returns empty array
    const result = checkForDuplicate({ ok: true, data: [] }, "app.log", 2500, 1310);
    expect(result.isDuplicate).toBe(false);
  });
});


describe("normaliseSeverity", () => {
  it('maps "error" to "critical"', () => {
    expect(normaliseSeverity("error")).toBe("critical");
  });

  it('maps "healthy" to "healthy"', () => {
    expect(normaliseSeverity("healthy")).toBe("healthy");
  });

  it('maps anything else to "warning"', () => {
    expect(normaliseSeverity("warning")).toBe("warning");
    expect(normaliseSeverity("unknown")).toBe("warning");
    expect(normaliseSeverity(undefined)).toBe("warning");
    expect(normaliseSeverity("")).toBe("warning");
  });
});


describe("buildPayload", () => {
  const baseState = {
    label:      "app.log",
    rcaClass:   "error",
    aiSummary:  "High error rate",
    fixText:    "1. Restart pod",
    stats:      { total: "2500", err: "1310", warn: "634", rate: "52.4" },
  };

  it("builds correct payload with string stats coerced to numbers", () => {
    const p = buildPayload(baseState);
    expect(p.source_name).toBe("app.log");
    expect(p.severity).toBe("critical");          // error → critical
    expect(p.total_entries).toBe(2500);            // string → int
    expect(p.error_count).toBe(1310);
    expect(p.warn_count).toBe(634);
    expect(p.error_rate).toBeCloseTo(52.4);
  });

  it("total_entries and error_count are integers", () => {
    const p = buildPayload(baseState);
    expect(Number.isInteger(p.total_entries)).toBe(true);
    expect(Number.isInteger(p.error_count)).toBe(true);
  });

  it("falls back to defaults when state is minimal", () => {
    const p = buildPayload({});
    expect(p.source_name).toBe("Unknown Source");
    expect(p.severity).toBe("warning");
    expect(p.total_entries).toBe(0);
    expect(p.error_count).toBe(0);
    expect(p.error_rate).toBe(0);
  });
});


describe("saveFlowDecision (full duplicate-check flow)", () => {
  it("returns action:save when API returns empty records", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      json: async () => ({ ok: true, data: [] }),
    });
    const result = await saveFlowDecision(fetchFn, "app.log", 2500, 1310);
    expect(result.action).toBe("save");
  });

  it("returns action:show_modal when duplicate found", async () => {
    const record = makeRecord();
    const fetchFn = vi.fn().mockResolvedValue({
      json: async () => ({ ok: true, data: [record] }),
    });
    const result = await saveFlowDecision(fetchFn, "app.log", 2500, 1310);
    expect(result.action).toBe("show_modal");
    expect(result.record.id).toBe("rec-abc-123");
  });

  it("returns action:save when API returns ok:false (fail open)", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      json: async () => ({ ok: false, error: "Supabase error" }),
    });
    const result = await saveFlowDecision(fetchFn, "app.log", 2500, 1310);
    expect(result.action).toBe("save");
  });

  it("returns action:save on network error (fail open)", async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error("Network error"));
    const result = await saveFlowDecision(fetchFn, "app.log", 2500, 1310);
    expect(result.action).toBe("save");
  });

  it("returns action:save after record was deleted (empty response)", async () => {
    // First call: record exists
    // Second call (after deletion): empty
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce({ json: async () => ({ ok: true, data: [makeRecord()] }) })
      .mockResolvedValueOnce({ json: async () => ({ ok: true, data: [] }) });

    const first  = await saveFlowDecision(fetchFn, "app.log", 2500, 1310);
    const second = await saveFlowDecision(fetchFn, "app.log", 2500, 1310);

    expect(first.action).toBe("show_modal");   // was in DB
    expect(second.action).toBe("save");         // deleted → clear to save
  });

  it("passes correct search query to fetch", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      json: async () => ({ ok: true, data: [] }),
    });
    await saveFlowDecision(fetchFn, "my app.log", 100, 50);
    const calledUrl = fetchFn.mock.calls[0][0];
    expect(calledUrl).toContain("search=");
    expect(calledUrl).toContain(encodeURIComponent("my app.log"));
    expect(calledUrl).toContain("limit=50");
  });
});