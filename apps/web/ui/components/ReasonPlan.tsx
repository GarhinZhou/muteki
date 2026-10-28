"use client";

import { Icon } from "@/components/Icon";
import { useT } from "@/lib/i18n";
import type { ReasonPlanView } from "@/lib/reasonPlan";

function verdictKey(verdict: string): string {
  if (verdict === "complete") return "reasonPlan.verdictComplete";
  if (verdict === "course_correct") return "reasonPlan.verdictCourseCorrect";
  return "reasonPlan.verdictExplore";
}

/** Readable projection of the Reason planner's JSON reply. */
export function ReasonPlan({
  plan,
  raw,
}: {
  plan: ReasonPlanView;
  raw: string;
}) {
  const t = useT();
  const verdictTone = plan.verdict === "complete"
    ? "complete"
    : plan.verdict === "course_correct"
      ? "correct"
      : "explore";

  return (
    <div className="reason-plan">
      <div className="reason-plan-head">
        <span className="reason-plan-title">
          <Icon name="cpu" size={12} />
          {t("reasonPlan.title")}
        </span>
        <span className={`reason-plan-verdict ${verdictTone}`}>
          {t(verdictKey(plan.verdict))}
        </span>
        {plan.goalMet ? (
          <span className="reason-plan-goalmet">{t("reasonPlan.goalMet")}</span>
        ) : null}
      </div>

      {plan.completeWhy ? (
        <div className="reason-plan-note">
          <b>{t("reasonPlan.completeWhy")}</b>
          <span>{plan.completeWhy}</span>
        </div>
      ) : null}
      {plan.drift ? (
        <div className="reason-plan-note warn">
          <b>{t("reasonPlan.drift")}</b>
          <span>{plan.drift}</span>
        </div>
      ) : null}

      {plan.audit.length > 0 ? (
        <details className="reason-plan-audit">
          <summary>{t("reasonPlan.audit", { n: plan.audit.length })}</summary>
          <ul>
            {plan.audit.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </details>
      ) : null}

      {plan.intents.length > 0 ? (
        <div className="reason-plan-intents">
          <div className="reason-plan-section-label">
            {t("reasonPlan.intents", { n: plan.intents.length })}
          </div>
          {plan.intents.map((intent) => (
            <div key={intent.id} className="reason-plan-intent">
              <div className="reason-plan-intent-head">
                <span className="reason-plan-id">{intent.id}</span>
                <span className={`reason-plan-wc wc-${intent.workerClass}`}>
                  {intent.workerClass}
                </span>
                {intent.from.length > 0 ? (
                  <span className="reason-plan-from">
                    {t("reasonPlan.from")} {intent.from.map((seq) => `#${seq}`).join(" ")}
                  </span>
                ) : null}
              </div>
              <div className="reason-plan-goal">{intent.goal}</div>
              {intent.rationale ? (
                <details className="reason-plan-why">
                  <summary>{t("reasonPlan.rationale")}</summary>
                  <p>{intent.rationale}</p>
                </details>
              ) : null}
              {(intent.routeHash
                || intent.branchId
                || intent.laneKey
                || intent.riskClass
                || intent.dependsOn.length > 0
                || intent.dupOf
                || intent.reopenBecause) ? (
                <div className="reason-plan-meta">
                  {intent.routeHash ? (
                    <span>{t("reasonPlan.route")}: <code>{intent.routeHash}</code></span>
                  ) : null}
                  {intent.branchId ? <span>branch: {intent.branchId}</span> : null}
                  {intent.laneKey ? <span>lane: {intent.laneKey}</span> : null}
                  {intent.riskClass ? <span>risk: {intent.riskClass}</span> : null}
                  {intent.dependsOn.length > 0 ? (
                    <span>depends: {intent.dependsOn.join(", ")}</span>
                  ) : null}
                  {intent.dupOf ? <span>dup: {intent.dupOf}</span> : null}
                  {intent.reopenBecause ? (
                    <span>{t("reasonPlan.reopen")}: {intent.reopenBecause}</span>
                  ) : null}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="reason-plan-empty">{t("reasonPlan.noIntents")}</div>
      )}

      {plan.pinnedFacts.length > 0 ? (
        <div className="reason-plan-pinned">
          <span className="reason-plan-section-label">{t("reasonPlan.pinned")}</span>
          {plan.pinnedFacts.map((seq) => (
            <span key={seq} className="reason-plan-fact">#{seq}</span>
          ))}
        </div>
      ) : null}

      <details className="reason-plan-raw">
        <summary>{t("reasonPlan.raw")}</summary>
        <pre>{raw}</pre>
      </details>
    </div>
  );
}
