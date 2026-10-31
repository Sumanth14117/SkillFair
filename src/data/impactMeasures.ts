/** Proposed pilot measures only. No SkillFair outcome data is currently available. */
export type ImpactMeasure = {
  id: string
  title: string
  purpose: string
  dataNeeded: string
}

export const impactMeasures: ImpactMeasure[] = [
  {
    id: 'screening-time',
    title: 'Recruiter review time',
    purpose: 'Assess whether evidence-focused profiles help recruiters review role-relevant information more efficiently.',
    dataNeeded: 'A consented pilot baseline and consistently timed review sessions.',
  },
  {
    id: 'evidence-usefulness',
    title: 'Evidence usefulness',
    purpose: 'Understand whether supporting excerpts help reviewers interpret skill mentions and uncertainty.',
    dataNeeded: 'Structured recruiter feedback paired with reviewed evidence records.',
  },
  {
    id: 'candidate-experience',
    title: 'Candidate experience',
    purpose: 'Evaluate whether candidates understand how their experience and skills are represented.',
    dataNeeded: 'Voluntary candidate feedback collected with clear notice and consent.',
  },
  {
    id: 'process-outcomes',
    title: 'Process outcomes',
    purpose: 'Study changes in screening and progression patterns without treating them as proof of fairness.',
    dataNeeded: 'A governed, appropriately reviewed pilot dataset and a pre-defined comparison method.',
  },
]
