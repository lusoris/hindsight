import { proxyDataplane, type MentalModelRouteContext } from "@/lib/dataplane-proxy";

/** Triggers a mental model refresh on the dataplane. */
export async function POST(request: Request, { params }: MentalModelRouteContext) {
  const { bankId, mentalModelId } = await params;

  // Optional body: { mode: "full" } for a one-off full refresh. A bodiless POST
  // (every caller before it existed) still refreshes in the model's own mode.
  const body = await request.json().catch(() => null);
  const mode = body?.mode === "full" ? "full" : undefined;

  return proxyDataplane(request, {
    action: "refreshing mental model",
    method: "POST",
    suffix: `/mental-models/${encodeURIComponent(mentalModelId)}/refresh`,
    bankId,
    required: [bankId, mentalModelId],
    missing: {
      error: "bank_id and mental_model_id are required",
      errorKey: "api.errors.validation.bankAndMentalModelIdRequired",
    },
    failure: {
      error: "Failed to refresh mental model",
      errorKey: "api.errors.mentalModels.refresh",
    },
    jsonBody: mode ? { mode } : undefined,
    upstreamTextAsError: true,
  });
}
