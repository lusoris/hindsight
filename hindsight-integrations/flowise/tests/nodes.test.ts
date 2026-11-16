import { describe, expect, it, vi, beforeEach } from "vitest";

// Mock the Hindsight client so init() never makes real HTTP calls. The
// instance is shared so individual tests can stub the methods.
const mockRetain = vi.fn();
const mockRecall = vi.fn();
const mockReflect = vi.fn();
const clientOpts: unknown[] = [];

vi.mock("@vectorize-io/hindsight-client", () => ({
  HindsightClient: class {
    retain = mockRetain;
    recall = mockRecall;
    reflect = mockReflect;
    constructor(opts: unknown) {
      clientOpts.push(opts);
    }
  },
}));

import * as retainModule from "../nodes/tools/HindsightRetain/HindsightRetain";
import * as recallModule from "../nodes/tools/HindsightRecall/HindsightRecall";
import * as reflectModule from "../nodes/tools/HindsightReflect/HindsightReflect";

const HindsightRetain = (retainModule as any).nodeClass;
const HindsightRecall = (recallModule as any).nodeClass;
const HindsightReflect = (reflectModule as any).nodeClass;

describe("HindsightRetain node", () => {
  beforeEach(() => {
    mockRetain.mockReset();
    mockRecall.mockReset();
    mockReflect.mockReset();
  });

  const node = new HindsightRetain();

  it("declares the expected metadata", () => {
    expect(node.label).toBe("Hindsight Retain");
    expect(node.name).toBe("hindsightRetain");
    expect(node.category).toBe("Tools");
    expect(node.icon).toBe("hindsight.png");
    expect(node.version).toBe(1.0);
  });

  it("requires the hindsightApi credential", () => {
    expect(node.credential.credentialNames).toEqual(["hindsightApi"]);
  });

  it("init() returns a structured tool exposing the retain schema", async () => {
    const credential = JSON.stringify({ apiUrl: "http://127.0.0.1:8888", apiKey: "" });
    const tool: any = await node.init({ credential, inputs: { bankId: "flowise-test" } }, "", {});
    expect(tool.name).toBe("hindsight_retain");
    expect(typeof tool.func).toBe("function");
    expect(tool.schema).toBeDefined();
  });

  it("identifies itself to the API with the integration's User-Agent", async () => {
    const credential = JSON.stringify({ apiUrl: "http://127.0.0.1:8888" });
    await node.init({ credential, inputs: {} }, "", {});
    expect(clientOpts.at(-1)).toMatchObject({ userAgent: "hindsight-flowise" });
  });

  it("forwards retain calls to the client with bankId, content, and tags", async () => {
    mockRetain.mockResolvedValue({ ok: true });
    const credential = JSON.stringify({ apiUrl: "http://127.0.0.1:8888" });
    const tool: any = await node.init({ credential, inputs: { bankId: "default-bank" } }, "", {});
    await tool.func({ bankId: "override-bank", content: "hello world", tags: ["t1"] });
    expect(mockRetain).toHaveBeenCalledWith("override-bank", "hello world", { tags: ["t1"] });
  });

  it("falls back to the default bankId when the agent does not pass one", async () => {
    mockRetain.mockResolvedValue({ ok: true });
    const credential = JSON.stringify({ apiUrl: "http://127.0.0.1:8888" });
    const tool: any = await node.init({ credential, inputs: { bankId: "fallback-bank" } }, "", {});
    await tool.func({ bankId: "", content: "hi" });
    expect(mockRetain).toHaveBeenCalledWith("fallback-bank", "hi", undefined);
  });
});

describe("HindsightRecall node", () => {
  beforeEach(() => {
    mockRetain.mockReset();
    mockRecall.mockReset();
    mockReflect.mockReset();
  });

  const node = new HindsightRecall();

  it("declares the expected metadata", () => {
    expect(node.label).toBe("Hindsight Recall");
    expect(node.name).toBe("hindsightRecall");
    expect(node.category).toBe("Tools");
  });

  it("exposes a default budget input with low/mid/high options", () => {
    const budget = node.inputs.find((i: { name: string }) => i.name === "budget");
    expect(budget).toBeDefined();
    const values = (budget.options as Array<{ name: string }>).map((o) => o.name);
    expect(values).toEqual(["low", "mid", "high"]);
  });

  it("init() forwards budget, maxTokens, and tags to the client", async () => {
    mockRecall.mockResolvedValue({ results: [] });
    const credential = JSON.stringify({ apiUrl: "http://127.0.0.1:8888" });
    const tool: any = await node.init(
      { credential, inputs: { bankId: "b1", budget: "high" } },
      "",
      {}
    );
    await tool.func({ bankId: "b2", query: "q", budget: "low", maxTokens: 256, tags: ["x"] });
    expect(mockRecall).toHaveBeenCalledWith("b2", "q", {
      budget: "low",
      maxTokens: 256,
      tags: ["x"],
    });
  });

  it("uses node default budget when the agent does not pass one", async () => {
    mockRecall.mockResolvedValue({ results: [] });
    const credential = JSON.stringify({ apiUrl: "http://127.0.0.1:8888" });
    const tool: any = await node.init(
      { credential, inputs: { bankId: "b1", budget: "high" } },
      "",
      {}
    );
    await tool.func({ bankId: "b1", query: "q" });
    expect(mockRecall).toHaveBeenCalledWith("b1", "q", { budget: "high" });
  });
});

describe("HindsightReflect node", () => {
  beforeEach(() => {
    mockRetain.mockReset();
    mockRecall.mockReset();
    mockReflect.mockReset();
  });

  const node = new HindsightReflect();

  it("declares the expected metadata", () => {
    expect(node.label).toBe("Hindsight Reflect");
    expect(node.name).toBe("hindsightReflect");
    expect(node.category).toBe("Tools");
  });

  it("init() forwards bankId, query, and budget to the client", async () => {
    mockReflect.mockResolvedValue({ text: "synthesis" });
    const credential = JSON.stringify({ apiUrl: "http://127.0.0.1:8888" });
    const tool: any = await node.init(
      { credential, inputs: { bankId: "b1", budget: "mid" } },
      "",
      {}
    );
    await tool.func({ bankId: "b2", query: "q", budget: "high" });
    expect(mockReflect).toHaveBeenCalledWith("b2", "q", { budget: "high" });
  });
});

describe("All three nodes share the hindsightApi credential", () => {
  it("uses the same credential name", () => {
    const r = new HindsightRetain();
    const c = new HindsightRecall();
    const f = new HindsightReflect();
    expect(r.credential.credentialNames).toEqual(["hindsightApi"]);
    expect(c.credential.credentialNames).toEqual(["hindsightApi"]);
    expect(f.credential.credentialNames).toEqual(["hindsightApi"]);
  });
});

// Literal inputs captured from the nodes at 263bd7162, before extracting the core.
const originalNodeInputs = [
  {
    Node: HindsightRecall,
    inputs: [
      {
        label: "Default Bank ID",
        name: "bankId",
        type: "string",
        description:
          "Default memory bank to recall from when the agent does not pass one. Banks are created on first use.",
        placeholder: "user-123",
        optional: true,
      },
      {
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
        description: "Default recall budget when the agent does not pass one.",
      },
    ],
  },
  {
    Node: HindsightReflect,
    inputs: [
      {
        label: "Default Bank ID",
        name: "bankId",
        type: "string",
        description:
          "Default memory bank to reflect on when the agent does not pass one. Banks are created on first use.",
        placeholder: "user-123",
        optional: true,
      },
      {
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
        description: "Default reflect budget when the agent does not pass one.",
      },
    ],
  },
  {
    Node: HindsightRetain,
    inputs: [
      {
        label: "Default Bank ID",
        name: "bankId",
        type: "string",
        description:
          "Default memory bank to retain into when the agent does not pass one. Banks are created on first use.",
        placeholder: "user-123",
        optional: true,
      },
    ],
  },
];

for (const { Node, inputs } of originalNodeInputs) {
  describe(new Node().type + " shared core contract", () => {
    it("preserves the complete input list", () => {
      expect(new Node().inputs).toStrictEqual(inputs);
    });

    it.each([
      ["icon", "hindsight.png"],
      ["category", "Tools"],
      ["version", 1.0],
      [
        "credential",
        {
          label: "Connect Credential",
          name: "credential",
          type: "credential",
          credentialNames: ["hindsightApi"],
        },
      ],
    ] as const)("preserves %s", (field, expected) => {
      expect(new Node()[field]).toStrictEqual(expected);
    });

    it("starts baseClasses with the node type", () => {
      const node = new Node();
      expect(node.baseClasses[0]).toBe(node.type);
    });

    it.each([
      { apiUrl: "http://127.0.0.1:8888", apiKey: "fixture-api-key" },
      { apiUrl: "http://127.0.0.1:8888", apiKey: "" },
      { apiUrl: "http://127.0.0.1:8888" },
      { apiUrl: "", apiKey: "fixture-api-key" },
      { apiUrl: "", apiKey: "" },
      {},
    ])("constructs the client from fixture %j", async (credential) => {
      clientOpts.length = 0;
      await new Node().init({ credential: JSON.stringify(credential) }, "", {});
      expect(clientOpts).toStrictEqual([
        {
          baseUrl: credential.apiUrl || "https://api.hindsight.vectorize.io",
          ...(credential.apiKey ? { apiKey: credential.apiKey } : {}),
          userAgent: "hindsight-flowise",
        },
      ]);
    });
  });
}

it("does not export nodeClass from the shared helper", async () => {
  const core = await import("../nodes/tools/HindsightCommon/core");
  expect(core).not.toHaveProperty("nodeClass");
});
