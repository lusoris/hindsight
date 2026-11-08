import { NextRequest, NextResponse } from "next/server";
import { sdk, lowLevelClient } from "@/lib/hindsight-client";
import { respondWithSdk } from "@/lib/sdk-response";
import { readTagFilter } from "@/lib/tag-filter-params";
import { requireQueryBankId } from "@/lib/route-validation";

/** Proxies one entity (`sdk.getEntity`) for the bank named by `?bank_id=`, forwarding the tag filter. */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ entityId: string }> }
) {
  const bankId = requireQueryBankId(request);
  if (bankId instanceof NextResponse) return bankId;

  const { entityId } = await params;
  const searchParams = request.nextUrl.searchParams;

  // Decode URL-encoded entityId in case it contains special chars
  const decodedEntityId = decodeURIComponent(entityId);

  const response = await sdk.getEntity({
    client: lowLevelClient,
    path: {
      bank_id: bankId,
      entity_id: decodedEntityId,
    },
    query: readTagFilter(searchParams),
  });
  return respondWithSdk(response, "Failed to get entity", { request });
}
