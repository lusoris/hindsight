import { NextRequest } from "next/server";
import { lowLevelClient, sdk } from "@/lib/hindsight-client";
import { respondWithBankSdk, type BankRouteContext } from "@/lib/bank-sdk-route";

/** Tests LLM connectivity for a bank. */
export async function POST(request: NextRequest, context: BankRouteContext) {
  return respondWithBankSdk(request, context, "Failed to test bank LLM connectivity", (bankId) =>
    sdk.testBankLlm({ client: lowLevelClient, path: { bank_id: bankId } })
  );
}
