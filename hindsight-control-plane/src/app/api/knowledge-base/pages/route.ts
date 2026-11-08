import { NextRequest } from "next/server";
import { createKnowledgeBaseNode } from "@/lib/knowledge-base-proxy";

/** Creates a knowledge-base page in the bank from the JSON body. */
export async function POST(request: NextRequest) {
  return createKnowledgeBaseNode(request, "pages", {
    error: "Failed to create page",
    errorKey: "api.errors.knowledgeBase.createPage",
  });
}
