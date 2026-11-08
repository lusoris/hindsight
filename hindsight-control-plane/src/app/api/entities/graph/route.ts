import { NextRequest, NextResponse } from "next/server";
import { sdk, lowLevelClient } from "@/lib/hindsight-client";
import { respondWithSdk } from "@/lib/sdk-response";
import { readTagFilter } from "@/lib/tag-filter-params";
import { requireQueryBankId } from "@/lib/route-validation";
import { readOptionalNumber } from "@/lib/query-params";

/** Fetches the bank's entity graph, narrowed by `limit`, `min_count` and the tag filter. */
export async function GET(request: NextRequest) {
  const bankId = requireQueryBankId(request);
  if (bankId instanceof NextResponse) return bankId;

  const searchParams = request.nextUrl.searchParams;
  const limit = readOptionalNumber(searchParams, "limit");
  const minCount = readOptionalNumber(searchParams, "min_count");

  const response = await sdk.getEntityGraph({
    client: lowLevelClient,
    path: { bank_id: bankId },
    query: { limit, min_count: minCount, ...readTagFilter(searchParams) },
  });
  return respondWithSdk(response, "Failed to fetch entity graph", { request });
}
