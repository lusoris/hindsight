import { NextRequest } from "next/server";
import { lowLevelClient, sdk } from "@/lib/hindsight-client";
import { respondWithBankSdk, type BankRouteContext } from "@/lib/bank-sdk-route";

/**
 * Proxy for the dataplane dry-run extraction endpoint: extract facts from sample
 * text without storing anything. Bank-scoped, so the prompt tester can reach it the
 * same way it reaches the prompt preview.
 */
export async function POST(request: NextRequest, context: BankRouteContext) {
  return respondWithBankSdk(request, context, "Failed to run dry-run extraction", async (bankId) =>
    sdk.dryRunExtractMemories({
      client: lowLevelClient,
      path: { bank_id: bankId },
      body: await request.json(),
    })
  );
}
