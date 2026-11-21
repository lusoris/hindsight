import { NextRequest } from "next/server";
import { sdk, lowLevelClient } from "@/lib/hindsight-client";
import { respondWithSdk } from "@/lib/sdk-response";
import { validateBankId } from "@/lib/route-validation";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ agentId: string }> }
) {
  const { agentId } = await params;
  const bankIdError = validateBankId(request, agentId);
  if (bankIdError) return bankIdError;
  const response = await sdk.getAgentStats({
    client: lowLevelClient,
    path: { bank_id: agentId },
  });
  return respondWithSdk(response, "Failed to fetch stats", { request });
}
