import { NextRequest, NextResponse } from "next/server";
import { sdk, lowLevelClient } from "@/lib/hindsight-client";
import { respondWithSdk } from "@/lib/sdk-response";
import { requireQueryBankId } from "@/lib/route-validation";

/** Regenerates one entity's observations (`sdk.regenerateEntityObservations`) for the bank named by `?bank_id=`. */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ entityId: string }> }
) {
  const bankId = requireQueryBankId(request);
  if (bankId instanceof NextResponse) return bankId;

  const { entityId } = await params;
  const decodedEntityId = decodeURIComponent(entityId);

  const response = await sdk.regenerateEntityObservations({
    client: lowLevelClient,
    path: {
      bank_id: bankId,
      entity_id: decodedEntityId,
    },
  });
  return respondWithSdk(response, "Failed to regenerate entity observations", { request });
}
