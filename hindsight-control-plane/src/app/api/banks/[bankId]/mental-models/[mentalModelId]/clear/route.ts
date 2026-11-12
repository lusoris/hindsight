import { proxyDataplane, type MentalModelRouteContext } from "@/lib/dataplane-proxy";

/** Clears a mental model's synthesized content on the dataplane. */
export async function POST(request: Request, { params }: MentalModelRouteContext) {
  const { bankId, mentalModelId } = await params;
  return proxyDataplane(request, {
    action: "clearing mental model",
    method: "POST",
    suffix: `/mental-models/${encodeURIComponent(mentalModelId)}/clear`,
    bankId,
    required: [bankId, mentalModelId],
    missing: {
      error: "bank_id and mental_model_id are required",
      errorKey: "api.errors.validation.bankAndMentalModelIdRequired",
    },
    failure: {
      error: "Failed to clear mental model",
      errorKey: "api.errors.mentalModels.clear",
    },
    upstreamTextAsError: true,
  });
}
