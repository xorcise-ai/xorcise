// Convenience aliases over the GENERATED schema (src/lib/api/schema.ts).
// Import these instead of reaching into `components['schemas'][...]` everywhere.
import type { components } from "./schema";

type S = components["schemas"];

export type AgentEntry = S["AgentEntry"];
export type AgentDeclaration = S["AgentDeclaration"];
/** A register/update result: the saved entry plus the server's warnings about it (#141). */
export type AgentSaveView = S["AgentSaveView"];
export type AgentHistoryEntry = S["AgentHistoryView"];
export type CatalogEntry = S["CatalogEntry"];
export type CatalogStatus = S["CatalogStatus"];
export type Announcement = S["Announcement"];
export type AnnouncementsResponse = S["AnnouncementsResponse"];
export type RunEntry = S["RunEntry"];
/** How a run is shown — label, tone and why it ended — computed once by the server
 *  (contracts.run.run_status) so the GUI and the CLI word every run identically (#165). */
export type RunStatus = S["RunStatus"];
export type RunCreate = S["RunCreate"];
export type RunCreatedEntry = S["RunCreatedEntry"];
export type GradeResult = S["GradeResult"];
export type RunResultView = S["RunResultView"];
export type RunStats = S["RunStats"];
/** One finished run as the performance views draw it, with how it counts (`bucket`), decided
 *  by the server (reporting.scores) — GET /results/runs. */
export type RunScore = S["RunScore"];
export type ScoreBucket = RunScore["bucket"];
/** Finished runs summarised per agent, mission version, or agent on a mission version —
 *  GET /results/summary. The median is over `scored` runs only. */
export type ScoreSummary = S["ScoreSummary"];

/** The FINAL answer /result, /report and /stats give for a run that is never graded: one cut short
 *  by an environment failure (`deploy_failed` / `crashed`), because the failure is not the agent's,
 *  or one that ended before its agent started (`cancelled` / `never_started`), because there was
 *  no attempt (#187).
 *  Declared in the OpenAPI schema (contracts.run.NotGradedView), so this is the generated type. */
export type NotGradedView = S["NotGradedView"];
/** The 202 /result, /report and /stats give while a terminal run's grade is being recorded. */
export type GradingView = S["GradingView"];

export function isNotGraded(data: unknown): data is NotGradedView {
  return (
    typeof data === "object" &&
    data !== null &&
    (data as { status?: unknown }).status === "not_graded"
  );
}
export type RunArtifact = S["RunArtifactView"];
export type RunEnvironment = S["RunEnvironmentView"];
export type ResultConditions = S["ResultConditions"];
export type ScoreBreakdown = S["ScoreBreakdown"];
export type CheckVerdict = S["CheckVerdict"];
export type CriterionScore = S["CriterionScore"];
export type ConfigView = S["ConfigView"];
export type JudgeConfigView = S["JudgeConfigView"];
export type CatalogConfigView = S["CatalogConfigView"];
export type CatalogConfigUpdate = S["CatalogConfigUpdate"];
export type NetworkConfigView = S["NetworkConfigView"];
export type NetworkConfigUpdate = S["NetworkConfigUpdate"];
export type ModelConfigUpdate = S["ModelConfigUpdate"];
export type TerrainModelConfigView = S["TerrainModelConfigView"];
export type TerrainModelConfigUpdate = S["TerrainModelConfigUpdate"];
export type JudgeTestResult = S["JudgeTestResult"];
export type MissionManifest = S["MissionManifest"];
export type IngestStarted = S["IngestStarted"];
export type IngestJobView = S["IngestJobView"];
export type MissionUpdateOut = S["MissionUpdateOut"];
/** DELETE /missions/{id}?purge=true: the mission is uninstalled whatever `image_outcome` says. */
export type MissionDeleteOut = S["MissionDeleteOut"];
export type SystemInfo = S["SystemInfo"];
export type PlaneStatus = S["PlaneStatus"];
export type FsListing = S["FsListing"];
export type FsEntry = S["FsEntry"];
export type RunEventsView = S["RunEventsView"];
export type AgentEvent = S["AgentEvent"];
/** One authored mission intel (id + text) — the unit of per-run intel disclosure. */
export type Intel = S["Intel"];
export type RawTraceRef = S["RawTraceRef"];
export type AgentEventKind = AgentEvent["kind"];
export type AdapterWarning = S["AdapterWarning"];
export type KindSupport = S["KindSupport"];
/** A harness adapter's declared telemetry capabilities (TOTAL over AgentEventKind). */
export type HarnessCapabilityProfile = S["HarnessCapabilityProfile"];
/** Registration-facing harness identity, visibility, and non-runnable launch preview. */
export type HarnessDescriptor = S["HarnessDescriptor"];
export type AttributionStatus = S["AttributionStatus"];
export type ResolvedTerrainV2 = S["ResolvedTerrainV2"];
export type TerrainGroup = S["TerrainGroup"];
export type TerrainNodeV2 = S["TerrainNodeV2"];
export type TerrainEdgeV2 = S["TerrainEdgeV2"];
export type TerrainUpdate = S["TerrainUpdate"];

export type PullJobStarted = S["PullJobStarted"];
/** Poll view of a background pull. `percent` is null while the total is unknown
 * (indeterminate UI) and is NOT monotonic — docker layer totals grow mid-pull. */
export type PullJobView = S["PullJobView"];
export type PruneReport = S["PruneReport"];
export type PruneCandidateView = S["PruneCandidateView"];
