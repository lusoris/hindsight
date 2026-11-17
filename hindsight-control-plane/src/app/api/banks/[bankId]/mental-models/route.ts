import { NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { validateBankId } from "@/lib/route-validation";
import type { BankRouteContext } from "@/lib/bank-sdk-route";
import { relayBankGet } from "@/lib/dataplane-proxy";
import { forwardParams } from "@/lib/query-params";

export async function GET(request: Request, context: BankRouteContext) {
  return relayBankGet(request, context, {
    failure: {
      error: "Failed to list mental models",
      errorKey: "api.errors.mentalModels.list",
    },
    action: "listing mental models",
    onUpstreamError: "log",
    suffix: (q) => {
      const query = forwardParams(q, {
        repeated: ["tags"],
        single: ["tags_match", "detail", "limit", "offset", "exclude_knowledge_pages"],
      }).toString();
      return `/mental-models${query ? `?${query}` : ""}`;
    },
  });
}

export async function POST(request: Request, { params }: { params: Promise<{ bankId: string }> }) {
  try {
    const { bankId } = await params;
    const bankIdError = validateBankId(request, bankId);
    if (bankIdError) return bankIdError;

    const body = await request.json();

    const response = await fetch(dataplaneBankUrl(bankId, "/mental-models"), {
      method: "POST",
      headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error("API error creating mental model:", errorText);
      return NextResponse.json(
        localizeApiErrorPayload(request, {
          error: errorText || "Failed to create mental model",
          errorKey: "api.errors.mentalModels.create",
        }),
        { status: response.status }
      );
    }

    const data = await response.json();
    // Returns operation_id - content is generated in background
    return NextResponse.json(data, { status: 202 });
  } catch (error) {
    console.error("Error creating mental model:", error);
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Failed to create mental model",
        errorKey: "api.errors.mentalModels.create",
      }),
      { status: 500 }
    );
  }
}
