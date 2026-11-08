import { NextRequest } from "next/server";
import { lowLevelClient, sdk } from "@/lib/hindsight-client";
import { respondWithBankSdk, type BankRouteContext } from "@/lib/bank-sdk-route";

/**
 * Proxy for the dataplane prompt-preview endpoint: render the messages an operation
 * would send for this bank, without calling an LLM or changing anything. The body
 * carries the mission (and other prompt-affecting settings) the caller is editing, so
 * an unsaved change can be previewed before it is saved.
 */
export async function POST(request: NextRequest, context: BankRouteContext) {
  return respondWithBankSdk(request, context, "Failed to preview prompt", async (bankId) =>
    sdk.previewPrompt({
      client: lowLevelClient,
      path: { bank_id: bankId },
      body: await request.json(),
    })
  );
}
