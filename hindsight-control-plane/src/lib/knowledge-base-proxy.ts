import { NextRequest, NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { requireQueryBankId } from "@/lib/route-validation";
import { relayUpstreamJson, type ApiFailure } from "@/lib/dataplane-proxy";

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
    // nosemgrep: rules_lgpl_javascript_ssrf_rule-node-ssrf -- host is the server-set DATAPLANE_URL, kind is the "folders" | "pages" literal, and the bank id is one encodeURIComponent path segment; "." and "..", which a URL parser resolves out of the bank path, get a 400 from requireQueryBankId and a RangeError from dataplaneBankUrl (tests/lib/knowledge-base-proxy.test.ts, tests/lib/hindsight-client.test.ts); category D, expires 2027-03-20, ends when this fetch moves to the generated SDK client
    const response = await fetch(dataplaneBankUrl(bankId, `/knowledge-base/${kind}`), {
      method: "POST",
      headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    return await relayUpstreamJson(response, response.status);
  } catch (error) {
    // Constant format string: `failure.error` goes in as a %s argument, so a `%` in it cannot
    // act as a format specifier. The printed line is the same as the old interpolation.
    console.error("%s:", failure.error, error);
    return NextResponse.json(localizeApiErrorPayload(request, failure), { status: 500 });
  }
}
