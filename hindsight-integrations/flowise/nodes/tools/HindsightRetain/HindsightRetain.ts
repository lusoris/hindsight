import { DynamicStructuredTool } from "@langchain/core/tools";
import { z } from "zod";

import { ICommonObject, INodeData } from "../../../src/Interface";
import * as common from "../HindsightCommon/core";

const RetainSchema = z.object({
  bankId: z.string().describe("The Hindsight memory bank to retain content into."),
  content: z
    .string()
    .describe("Free-text content to store. Hindsight extracts facts asynchronously."),
  tags: z
    .array(z.string())
    .optional()
    .describe("Optional list of tags to apply to the retained memory."),
});

class HindsightRetain_Tools extends common.HindsightToolNode {
  constructor() {
    super({
      label: "Hindsight Retain",
      name: "hindsightRetain",
      type: "HindsightRetain",
      inputs: [common.defaultBankInput("retain into")],
      description:
        "Store free-text content as long-term memory in a Hindsight bank. Hindsight extracts facts asynchronously after the call returns.",
    });
  }

  async init(nodeData: INodeData, _: string, options: ICommonObject): Promise<unknown> {
    const { client, defaultBankId } = await common.createToolContext(nodeData, options);

    return new DynamicStructuredTool({
      name: "hindsight_retain",
      description:
        "Store free-text content into a Hindsight memory bank. Use this to retain facts about a user, decisions made, or anything else worth remembering across conversations. Hindsight extracts structured facts asynchronously.",
      schema: RetainSchema as any,
      func: async ({
        bankId,
        content,
        tags,
      }: {
        bankId: string;
        content: string;
        tags?: string[];
      }) => {
        const targetBank = bankId || defaultBankId;
        if (!targetBank) {
          return "Error: bankId is required (no default bank configured on the node).";
        }
        const opts = tags && tags.length ? { tags } : undefined;
        const response = await client.retain(targetBank, content, opts);
        return JSON.stringify({ bankId: targetBank, success: true, response });
      },
    });
  }
}

module.exports = { nodeClass: HindsightRetain_Tools };
