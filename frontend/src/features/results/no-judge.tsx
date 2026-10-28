/** Stands in for a judge score when the judge never ran (#143). A degraded judge contributes 0
 *  to Overall by design, so "0%" here would read as the judge scoring the run a zero. The words
 *  match the CLI's "No judge" column. */
export function NoJudge() {
  return (
    <span
      className="text-warning"
      title="The judge never ran, so it contributed 0 to Overall: this run scored 50% at most."
    >
      No judge
    </span>
  );
}
