import { test } from "node:test";
import assert from "node:assert/strict";
import { currentPeriod, date, parseTenge, percent, periodLabel, tenge } from "./format.ts";

test("tenge", () => {
  assert.equal(tenge(123456789), "1 234 567,89 ₸");
  assert.equal(tenge(-5), "−0,05 ₸");
  assert.equal(tenge(null), "—");
});

test("parseTenge", () => {
  assert.equal(parseTenge("1 234,5"), 123450);
  assert.equal(parseTenge("85000"), 8500000);
  assert.equal(parseTenge("1.005"), null);
  assert.equal(parseTenge("abc"), null);
});

test("misc", () => {
  assert.equal(percent("0.0123"), "1,2%");
  assert.equal(date("2026-08-15"), "15.08.2026");
  assert.equal(currentPeriod(new Date(2026, 9, 6)), "2026H2");
  assert.equal(periodLabel("2026H1"), "1 полугодие 2026");
});
