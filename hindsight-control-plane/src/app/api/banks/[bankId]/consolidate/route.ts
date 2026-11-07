import { sdk, lowLevelClient } from "@/lib/hindsight-client";
import { validateBankId } from "@/lib/route-validation";
import { respondWithSdk } from "@/lib/sdk-response";

export async function POST(request: Request, { params }: { params: Promise<{ bankId: string }> }) {
  const { bankId } = await params;
  const bankIdError = validateBankId(request, bankId);
  if (bankIdError) return bankIdError;

  const response = await sdk.triggerConsolidation({
    client: lowLevelClient,
    path: { bank_id: bankId },
  });
  return respondWithSdk(response, "Failed to trigger consolidation", { request });
}
