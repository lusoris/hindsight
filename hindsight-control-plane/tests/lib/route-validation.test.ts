import { describe, expect, it } from "vitest";
import { NextRequest, NextResponse } from "next/server";
import { requireQueryBankId } from "@/lib/route-validation";

describe("requireQueryBankId", () => {
  it("returns the bank id when present", () => {
    const req = new NextRequest("http://localhost/api/x?bank_id=b1");
    expect(requireQueryBankId(req)).toBe("b1");
  });

  it.each([
    ["absent", "http://localhost/api/x"],
    ["empty", "http://localhost/api/x?bank_id="],
  ])("returns the 400 bank_id payload when bank_id is %s", async (_case, url) => {
    const result = requireQueryBankId(new NextRequest(url));
    expect(result).toBeInstanceOf(NextResponse);
    const response = result as NextResponse;
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: "bank_id is required" });
  });

  it.each([
    ["bank_id wins over agent_id", "?bank_id=b1&agent_id=a1", "b1"],
    ["agent_id used when bank_id is absent", "?agent_id=a1", "a1"],
    ["agent_id used when bank_id is empty", "?bank_id=&agent_id=a1", "a1"],
  ])("honours the names order: %s", (_case, query, expected) => {
    const req = new NextRequest(`http://localhost/api/x${query}`);
    expect(requireQueryBankId(req, ["bank_id", "agent_id"])).toBe(expected);
  });
});
