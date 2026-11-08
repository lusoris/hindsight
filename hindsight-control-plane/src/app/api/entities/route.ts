import { NextRequest, NextResponse } from "next/server";
import { sdk, lowLevelClient } from "@/lib/hindsight-client";
import { respondWithSdk } from "@/lib/sdk-response";
import { readTagFilter } from "@/lib/tag-filter-params";
import { requireQueryBankId } from "@/lib/route-validation";
import { readOptionalNumber } from "@/lib/query-params";

/** Lists the bank's entities, paged by `limit`/`offset` and narrowed by the tag filter. */
export async function GET(request: NextRequest) {
  const bankId = requireQueryBankId(request);
  if (bankId instanceof NextResponse) return bankId;

  const searchParams = request.nextUrl.searchParams;
  const limit = readOptionalNumber(searchParams, "limit");
  const offset = readOptionalNumber(searchParams, "offset");

  const response = await sdk.listEntities({
    client: lowLevelClient,
    path: { bank_id: bankId },
    query: { limit, offset, ...readTagFilter(searchParams) },
  });
  return respondWithSdk(response, "Failed to list entities", { request });
}
