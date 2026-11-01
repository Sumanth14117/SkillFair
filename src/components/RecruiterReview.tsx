import { ArrowDown, ClipboardCheck, ShieldCheck } from 'lucide-react'
import CareerOrchestrator, { type CareerOrchestratorProps } from './CareerOrchestrator'
import { RECRUITER_REVIEW_WORKSPACE_ID } from '../recruiterReview'
import '../styles/recruiter-review.css'

type Props = Omit<CareerOrchestratorProps, 'mode' | 'onOpenOrchestrator'> & {
  onOpenOrchestrator: () => void
}

export default function RecruiterReview(props: Props) {
  return <div className="recruiter-review-page">
    <section className="panel recruiter-review-intro">
      <div className="recruiter-review-intro-icon"><ClipboardCheck size={20} /></div>
      <div className="recruiter-review-intro-copy">
        <span className="eyebrow"><span className="eyebrow-line" /> HUMAN-LED REVIEW</span>
        <h1>Recruiter Review</h1>
        <p>Review evidence, role criteria, fairness prompts, and the recorded workflow decision in one place.</p>
      </div>
      <a className="button-primary" href={`#${RECRUITER_REVIEW_WORKSPACE_ID}`}>Explore workspace <ArrowDown size={15} /></a>
      <div className="recruiter-review-note"><ShieldCheck size={15} /><span>Only backend-backed Career Orchestrator workflows appear here. Overview profiles are illustrative and are not candidate records.</span></div>
    </section>
    <div id={RECRUITER_REVIEW_WORKSPACE_ID} className="recruiter-review-workspace">
      <CareerOrchestrator {...props} mode="review" onOpenOrchestrator={props.onOpenOrchestrator} />
    </div>
  </div>
}
