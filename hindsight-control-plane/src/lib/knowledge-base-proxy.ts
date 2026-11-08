import { NextRequest, NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { requireQueryBankId } from "@/lib/route-validation";
import type { ApiFailure } from "@/lib/dataplane-proxy";

/** Creates one knowledge-base node (folder or page) in the `?bank_id=` bank from the JSON body. */
export async function createKnowledgeBaseNode(
  request: NextRequest,
  kind: "folders" | "pages",
  failure: ApiFailure
): Promise<NextResponse> {
  try {
    const bankId = requireQueryBankId(request);
    if (bankId instanceof NextResponse) return bankId;

    const body = await request.json();
    const response = await fetch(dataplaneBankUrl(bankId, `/knowledge-base/${kind}`), {
      method: "POST",
      headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      return NextResponse.json(error, { status: response.status });
    }
    return NextResponse.json(await response.json(), { status: response.status });
  } catch (error) {
    // Constant format string: `failure.error` goes in as a %s argument, so a `%` in it cannot
    // act as a format specifier. The printed line is the same as the old interpolation.
    console.error("%s:", failure.error, error);
    return NextResponse.json(localizeApiErrorPayload(request, failure), { status: 500 });
  }
}
