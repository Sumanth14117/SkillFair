export type DashboardWorkflowStatus = 'awaiting_human_review' | 'revise_required' | 'stopped' | 'complete'
export type DashboardReviewDecision = 'pending' | 'proceed' | 'revise' | 'stop'

export type DashboardOverview = {
  generated_at: string
  summary: {
    saved_analyses: number
    total_workflows: number
    pending_reviews: number
    completed_reviews: number
  }
  workflow_status_distribution: { status: DashboardWorkflowStatus; count: number }[]
  review_decision_distribution: { decision: DashboardReviewDecision; count: number }[]
  recent_workflows: {
    run_id: string
    job_title: string
    workflow_status: DashboardWorkflowStatus
    review_decision: DashboardReviewDecision
    created_at: string
    updated_at: string
  }[]
  recent_activity: {
    event_type: 'workflow_created' | 'review_started' | 'review_decided'
    workflow_id: string
    job_title: string
    occurred_at: string
    workflow_status: DashboardWorkflowStatus
    decision: 'proceed' | 'revise' | 'stop' | null
    description: string
  }[]
}
