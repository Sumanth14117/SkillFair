import { BookOpen, LockKeyhole, ShieldCheck } from 'lucide-react'

const guides = [
  ['Skill Passport', 'Upload a resume and choose local deterministic analysis or an explicitly consented external AI provider when one is configured. Evidence excerpts are retained with detected skills; detections are mentions, not proof of proficiency.'],
  ['Job Audit', 'Review job descriptions and structured requirements for wording that may merit human attention. Findings are advisory and do not determine legal compliance.'],
  ['Bias Audit', 'Run a deterministic review of role criteria, save the result, and record your review selections. The audit does not infer candidate demographics or certify fairness.'],
  ['Job Matching', 'Compare saved skill evidence with job requirements. Results explain evidence matches and gaps; they are not rankings or hiring recommendations.'],
  ['Human review', 'Career Orchestrator brings workflow context and audit information together. Proceed, Revise, or Stop are explicit human decisions; the service does not make hiring decisions.'],
  ['Learning Pathways', 'A completed pathway is made available only after a committed Proceed decision. It suggests learning activities and does not certify qualification.'],
  ['Impact Measurement', 'Review aggregate operational and voluntary feedback measures with sample sizes and availability labels. These observations do not demonstrate fairness or improved hiring outcomes.'],
]

export default function HelpCenter() {
  return <div className="utility-page help-page"><span className="eyebrow"><span className="eyebrow-line" /> SKILLFAIR GUIDE</span><h1>Help and product guide</h1><p>SkillFair is a skills-first recruitment prototype that helps people inspect evidence and role criteria while keeping decisions with human reviewers.</p>
    <div className="help-callout"><LockKeyhole size={18} /><p>Saved analyses, workflows, audits, and notifications are scoped to the signed-in account. Resume files and complete extracted resume text are not stored as saved analysis records. Browser-local preferences and drafts stay in this browser.</p></div>
    <section className="help-guides"><h2><BookOpen size={17} />Using SkillFair</h2>{guides.map(([title, body]) => <article key={title}><h3>{title}</h3><p>{body}</p></article>)}</section>
    <section className="help-callout help-limitations"><ShieldCheck size={18} /><div><b>Deterministic rules and AI-assisted discovery</b><p>Matching, Job Audit, and Bias Audit use explicit deterministic logic. Optional AI-assisted Skills Discovery sends extracted resume text to the configured provider only after per-request consent. Provider handling is outside SkillFair's control. Automated detection can miss or misclassify information; review the source evidence yourself.</p></div></section>
  </div>
}
