import { DynamicStructuredTool } from "@langchain/core/tools";
import { HindsightClient } from "@vectorize-io/hindsight-client";

import { ICommonObject, INode, INodeData, INodeParams } from "../../../src/Interface";
import { getBaseClasses, getCredentialData, getCredentialParam } from "../../../src/utils";

/**
 * Shared node metadata, inputs, and client setup for the three Hindsight tools.
 * The node directories import this module by relative path. Copy HindsightCommon/
 * with them into Flowise; the README's Hindsight* glob includes this directory.
 * This helper exports no nodeClass, so Flowise does not register it as a node.
 */
export const USER_AGENT = "hindsight-flowise";

export type Budget = "low" | "mid" | "high";

export abstract class HindsightToolNode implements INode {
  label: string;
  name: string;
  version: number;
  description: string;
  type: string;
  icon: string;
  category: string;
  baseClasses: string[];
  credential: INodeParams;
  inputs: INodeParams[];

  constructor(
    node: Pick<INode, "label" | "name" | "type" | "description"> & { inputs: INodeParams[] }
  ) {
    this.label = node.label;
    this.name = node.name;
    this.version = 1.0;
    this.type = node.type;
    this.icon = "hindsight.png";
    this.category = "Tools";
    this.description = node.description;
    this.credential = {
      label: "Connect Credential",
      name: "credential",
      type: "credential",
      credentialNames: ["hindsightApi"],
    };
    this.inputs = node.inputs;
    this.baseClasses = [this.type, ...getBaseClasses(DynamicStructuredTool)];
  }

  abstract init(nodeData: INodeData, input: string, options: ICommonObject): Promise<unknown>;
}

export function defaultBankInput(verbPhrase: string): INodeParams {
  return {
    label: "Default Bank ID",
    name: "bankId",
    type: "string",
    description: `Default memory bank to ${verbPhrase} when the agent does not pass one. Banks are created on first use.`,
    placeholder: "user-123",
    optional: true,
  };
}

export function defaultBudgetInput(operation: string): INodeParams {
  return {
    label: "Default Budget",
    name: "budget",
    type: "options",
    options: [
      { label: "Low", name: "low" },
      { label: "Mid", name: "mid" },
      { label: "High", name: "high" },
    ],
    default: "mid",
    optional: true,
    description: `Default ${operation} budget when the agent does not pass one.`,
  };
}

export async function createHindsightClient(
  nodeData: INodeData,
  options: ICommonObject
): Promise<HindsightClient> {
  const credentialData = await getCredentialData(nodeData.credential ?? "", options);
  const apiUrl =
    getCredentialParam("apiUrl", credentialData, nodeData) || "https://api.hindsight.vectorize.io";
  const apiKey = getCredentialParam("apiKey", credentialData, nodeData);

  return new HindsightClient({
    baseUrl: apiUrl,
    ...(apiKey ? { apiKey } : {}),
    userAgent: USER_AGENT,
  });
}

export async function createToolContext(
  nodeData: INodeData,
  options: ICommonObject
): Promise<{
  client: HindsightClient;
  defaultBankId: string;
  defaultBudget: Budget;
}> {
  return {
    client: await createHindsightClient(nodeData, options),
    defaultBankId: (nodeData.inputs?.bankId as string) || "",
    defaultBudget: ((nodeData.inputs?.budget as string) || "mid") as Budget,
  };
}
