/**
 * Parse the Reason planner's raw JSON reply into a typed plan for the
 * conversation. The planner is asked for JSON, so its reply currently lands in
 * the thread as a wall of text; the card view is the readable projection while
 * the original text stays available behind a disclosure.
 */

export interface ReasonPlanIntent {
  id: string;
  goal: string;
  workerClass: string;
  from: number[];
  rationale: string;
  routeHash: string;
  branchId: string;
  laneKey: string;
  riskClass: string;
  dependsOn: string[];
  dupOf: string;
  reopenBecause: string;
}

export interface ReasonPlanView {
  verdict: string;
  goalMet: boolean;
  intents: ReasonPlanIntent[];
  pinnedFacts: number[];
  audit: string[];
  completeWhy: string;
  drift: string;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function numberList(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  const out: number[] = [];
  for (const item of value) {
    const n = typeof item === "number" ? item : Number.parseInt(String(item), 10);
    if (Number.isFinite(n) && n > 0 && !out.includes(n)) out.push(n);
  }
  return out;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .map((item) => item.trim());
}

function extractJsonObject(source: string): string | null {
  // Planner replies are sometimes wrapped in prose / ```json fences.
  const stripped = source.replace(/```(?:json)?/gi, "");
  const start = stripped.indexOf("{");
  if (start < 0) return null;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < stripped.length; i += 1) {
    const ch = stripped[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return stripped.slice(start, i + 1);
    }
  }
  return null;
}

export function parseReasonPlan(source: string): ReasonPlanView | null {
  const json = extractJsonObject(String(source || ""));
  if (!json) return null;
  let data: unknown;
  try {
    data = JSON.parse(json);
  } catch {
    return null;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const plan = data as Record<string, unknown>;
  const rawIntents = Array.isArray(plan.intents) ? plan.intents : null;
  const hasShape = (
    rawIntents !== null
    || typeof plan.verdict === "string"
    || Array.isArray(plan.audit)
    || Array.isArray(plan.pinned_facts)
  );
  if (!hasShape) return null;

  const intents: ReasonPlanIntent[] = (rawIntents || [])
    .filter((item): item is Record<string, unknown> => (
      !!item && typeof item === "object" && !Array.isArray(item)
    ))
    .map((item, index) => {
      const from = numberList(item.from ?? item.from_facts);
      return {
        id: text(item.id) || text(item.intent_id) || `I${index + 1}`,
        goal: text(item.goal),
        workerClass: text(item.worker_class) || text(item.workerClass) || "code",
        from,
        rationale: text(item.rationale),
        routeHash: text(item.route_hash) || text(item.routeHash),
        branchId: text(item.branch_id) || text(item.branchId),
        laneKey: text(item.lane_key) || text(item.laneKey),
        riskClass: text(item.risk_class) || text(item.riskClass),
        dependsOn: stringList(item.depends_on ?? item.dependsOn),
        dupOf: text(item.dup_of) || text(item.dupOf),
        reopenBecause: text(item.reopen_because) || text(item.reopenBecause),
      };
    })
    .filter((item) => item.goal.length > 0 || item.rationale.length > 0);

  return {
    verdict: text(plan.verdict).toLowerCase(),
    goalMet: plan.goal_met === true || plan.goalMet === true,
    intents,
    pinnedFacts: numberList(plan.pinned_facts ?? plan.pinnedFacts),
    audit: stringList(plan.audit),
    completeWhy: text(plan.complete_why) || text(plan.completeWhy),
    drift: text(plan.drift),
  };
}
