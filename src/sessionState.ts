import type { SkillsDiscoveryResult } from './types/skills'
import type { CareerWorkflowDraft } from './components/CareerOrchestrator'
import { sections } from './data/demoData.ts'

export type AppPage = typeof sections[number]['name'] | 'Job Matching'
export type SkillFairSession = {
  analysisId: string | null
  skillCount: number
  analysisMode: SkillsDiscoveryResult['analysis_mode'] | null
  page: AppPage
  runId: string | null
  careerDraft: CareerWorkflowDraft
}

const STORAGE_KEY = 'skillfair.session.v1'
const DEFAULT_DRAFT: CareerWorkflowDraft = { jobTitle: '', jobDescription: '', geography: '', essentialSkills: '', preferredSkills: '' }
const VALID_PAGES = new Set<AppPage>([...sections.map((item) => item.name), 'Job Matching'])
const LOCATOR_PATTERN = /^[A-Za-z0-9_-]{16,128}$/
const DRAFT_LIMITS: Record<keyof CareerWorkflowDraft, number> = { jobTitle: 160, jobDescription: 10000, geography: 120, essentialSkills: 7000, preferredSkills: 7000 }

export const emptySkillFairSession = (): SkillFairSession => ({
  analysisId: null, skillCount: 0, analysisMode: null, page: 'Overview', runId: null, careerDraft: { ...DEFAULT_DRAFT },
})

export function isValidSessionLocator(value: unknown): value is string {
  return typeof value === 'string' && LOCATOR_PATTERN.test(value)
}

function parseSession(value: unknown): SkillFairSession | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as Record<string, unknown>
  if (candidate.version !== 1) return null
  const page = candidate.page
  const draft = candidate.careerDraft
  if (typeof page !== 'string' || !VALID_PAGES.has(page as AppPage) || !draft || typeof draft !== 'object') return null
  const rawDraft = draft as Record<string, unknown>
  const draftKeys: (keyof CareerWorkflowDraft)[] = ['jobTitle', 'jobDescription', 'geography', 'essentialSkills', 'preferredSkills']
  if (draftKeys.some((key) => typeof rawDraft[key] !== 'string' || (rawDraft[key] as string).length > DRAFT_LIMITS[key])) return null
  if (candidate.analysisId !== null && !isValidSessionLocator(candidate.analysisId)) return null
  if (candidate.runId !== null && !isValidSessionLocator(candidate.runId)) return null
  if (!Number.isInteger(candidate.skillCount) || (candidate.skillCount as number) < 0 || (candidate.skillCount as number) > 10000) return null
  if (candidate.analysisMode !== null && candidate.analysisMode !== 'ai' && candidate.analysisMode !== 'local' && candidate.analysisMode !== 'development') return null
  if ((!candidate.analysisId && (candidate.skillCount !== 0 || candidate.analysisMode !== null)) || (candidate.analysisId && candidate.analysisMode === null)) return null
  return {
    analysisId: candidate.analysisId as string | null,
    skillCount: candidate.skillCount as number,
    analysisMode: candidate.analysisMode as SkillsDiscoveryResult['analysis_mode'] | null,
    page: page as AppPage,
    runId: candidate.runId as string | null,
    careerDraft: Object.fromEntries(draftKeys.map((key) => [key, rawDraft[key]])) as CareerWorkflowDraft,
  }
}

export function readSkillFairSession(): SkillFairSession {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return emptySkillFairSession()
    const parsed = parseSession(JSON.parse(raw) as unknown)
    if (parsed) return parsed
    try { window.sessionStorage.removeItem(STORAGE_KEY) } catch { /* Storage may be unavailable. */ }
  } catch { /* Private browsing and storage policy can block access. */ }
  return emptySkillFairSession()
}

export function writeSkillFairSession(session: SkillFairSession): void {
  const safeAnalysisId = session.analysisId && isValidSessionLocator(session.analysisId) ? session.analysisId : null
  const safe: SkillFairSession = {
    analysisId: safeAnalysisId,
    skillCount: safeAnalysisId && Number.isInteger(session.skillCount) && session.skillCount >= 0 ? session.skillCount : 0,
    analysisMode: safeAnalysisId && (session.analysisMode === 'ai' || session.analysisMode === 'local' || session.analysisMode === 'development') ? session.analysisMode : null,
    page: VALID_PAGES.has(session.page) ? session.page : 'Overview',
    runId: session.runId && isValidSessionLocator(session.runId) ? session.runId : null,
    careerDraft: Object.fromEntries(Object.keys(DEFAULT_DRAFT).map((key) => {
      const draftKey = key as keyof CareerWorkflowDraft
      const value = session.careerDraft[draftKey]
      return [draftKey, typeof value === 'string' ? value.slice(0, DRAFT_LIMITS[draftKey]) : '']
    })) as CareerWorkflowDraft,
  }
  try { window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, ...safe })) } catch { /* Persistence is best-effort only. */ }
}
