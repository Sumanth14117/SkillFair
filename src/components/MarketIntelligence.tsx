import { useEffect, useRef, useState, type FormEvent } from 'react'
import { AlertCircle, BarChart3, CalendarDays, Database, Globe2, LoaderCircle, MapPin, ShieldCheck, Sparkles } from 'lucide-react'
import { fetchMarketIntelligence, fetchProviderStatus, type MarketCountryCode, type MarketDataSource, type MarketErrorKind, type MarketInsight, type MarketIntelligenceResult, type MarketProviderStatus } from '../marketIntelligenceClient'
import '../styles/market-intelligence.css'

type ErrorKind = MarketErrorKind

function formatTimestamp(value: string): string {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  }).format(parsed)
}

function MarketSource({ source }: { source: MarketDataSource }) {
  return <article className="market-source-card">
    <div className="market-source-heading"><div className="market-source-icon"><Database size={16} /></div><div><h4>{source.source_name}</h4><span>{source.source_type} · {source.verification_status}</span></div><ShieldCheck size={16} className="market-source-verified" aria-label="Verified source" /></div>
    <dl className="market-source-meta"><div><dt>Retrieved</dt><dd><CalendarDays size={13} />{formatTimestamp(source.retrieved_at)}</dd></div>{source.published_at && <div><dt>Published</dt><dd><CalendarDays size={13} />{formatTimestamp(source.published_at)}</dd></div>}{source.geography && <div><dt>Source geography</dt><dd><MapPin size={13} />{source.geography}</dd></div>}<div className="market-source-reference"><dt>Source reference</dt><dd>{source.source_reference}</dd></div></dl>
  </article>
}

function InsightCard({ insight, source }: { insight: MarketInsight; source?: MarketDataSource }) {
  return <article className="market-insight-card">
    <div className="market-insight-kicker"><span>{insight.category.replaceAll('_', ' ')}</span>{insight.period && <span>{insight.period}</span>}</div>
    <h3>{insight.title}</h3><p>{insight.summary}</p>
    {insight.value !== null && <div className="market-insight-value"><strong>{insight.value.toLocaleString()}</strong>{insight.unit && <span>{insight.unit}</span>}</div>}
    <div className="market-insight-attribution"><ShieldCheck size={14} /><span>Source: <b>{source?.source_name ?? insight.source_id}</b></span>{insight.geography && <span className="market-insight-geography"><Globe2 size={12} />{insight.geography}</span>}</div>
    <dl className="market-insight-method"><div><dt>Calculation</dt><dd>{insight.calculation_method}</dd></div><div><dt>Data quality</dt><dd>{insight.data_quality}</dd></div></dl>
  </article>
}

const MARKET_COUNTRIES: { code: MarketCountryCode; name: string }[] = [
  { code: 'in', name: 'India' }, { code: 'gb', name: 'United Kingdom' }, { code: 'us', name: 'United States' },
  { code: 'au', name: 'Australia' }, { code: 'ca', name: 'Canada' }, { code: 'sg', name: 'Singapore' },
  { code: 'za', name: 'South Africa' }, { code: 'at', name: 'Austria' }, { code: 'be', name: 'Belgium' },
  { code: 'br', name: 'Brazil' }, { code: 'ch', name: 'Switzerland' }, { code: 'de', name: 'Germany' },
  { code: 'es', name: 'Spain' }, { code: 'fr', name: 'France' }, { code: 'it', name: 'Italy' },
  { code: 'mx', name: 'Mexico' }, { code: 'nl', name: 'Netherlands' }, { code: 'nz', name: 'New Zealand' }, { code: 'pl', name: 'Poland' },
]

export default function MarketIntelligence() {
  const [jobTitle, setJobTitle] = useState('')
  const [geography, setGeography] = useState('')
  const [countryCode, setCountryCode] = useState<MarketCountryCode>('in')
  const [skillQueries, setSkillQueries] = useState('')
  const [result, setResult] = useState<MarketIntelligenceResult | null>(null)
  const [validation, setValidation] = useState('')
  const [error, setError] = useState<{ kind: ErrorKind; message: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [providerStatus, setProviderStatus] = useState<MarketProviderStatus | null>(null)
  const [providerStatusLoading, setProviderStatusLoading] = useState(true)
  const requestInFlight = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    fetchProviderStatus(controller.signal).then((status) => {
      if (!controller.signal.aborted) setProviderStatus(status)
    }).catch(() => {
      if (!controller.signal.aborted) setProviderStatus(null)
    }).finally(() => {
      if (!controller.signal.aborted) setProviderStatusLoading(false)
    })
    return () => controller.abort()
  }, [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (requestInFlight.current || loading) return
    const title = jobTitle.trim()
    const place = geography.trim()
    if (!title) { setValidation('Enter a job title to check market-data availability.'); return }
    if (title.length > 160) { setValidation('Job title must be 160 characters or fewer.'); return }
    if (place.length > 120) { setValidation('Geography must be 120 characters or fewer.'); return }

    requestInFlight.current = true
    setLoading(true)
    setError(null)
    setValidation('')
    setResult(null)
    try {
      const skills = [...new Set(skillQueries.split(/[\n,]+/).map((value) => value.trim()).filter(Boolean))]
      if (skills.length > 5) { setValidation('Enter no more than five skill keywords.'); return }
      if (skills.some((skill) => skill.length > 100)) { setValidation('Each skill keyword must be 100 characters or fewer.'); return }
      const payload = { job_title: title, country_code: countryCode, ...(place ? { geography: place } : {}), ...(skills.length ? { skill_queries: skills } : {}) }
      setResult(await fetchMarketIntelligence(payload))
    } catch (reason) {
      const failure = reason as Error & { kind?: ErrorKind }
      setError({ kind: failure.kind ?? 'service', message: failure.message || 'Market Intelligence could not be loaded. Please try again.' })
    } finally {
      requestInFlight.current = false
      setLoading(false)
    }
  }

  const sourceById = new Map(result?.sources.map((source) => [source.source_id, source]) ?? [])
  return <div className="market-page">
    <header className="market-heading"><div><span className="eyebrow"><span className="eyebrow-line" /> VERIFIED MARKET CONTEXT</span><h1>Market Intelligence</h1><p>Check whether sourced market insights are available for a role and geography.</p></div><span className="market-heading-label"><BarChart3 size={16} />Source-aware · No estimates</span></header>
    <aside className="market-advisory" role="note"><span><ShieldCheck size={18} /></span><p><b>Insights require verified market data.</b> Skill demand, salaries, job counts, and employer information appear only when supported by a connected, attributed source.</p></aside>

    <div className="market-layout">
      <form className="panel market-form" onSubmit={submit} noValidate aria-busy={loading}>
        <div className="market-form-heading"><span>01</span><div><h2>Explore a role</h2><p>Specify the job and optional geography.</p></div></div>
        <label htmlFor="market-job-title">Job title</label>
        <input id="market-job-title" name="job_title" value={jobTitle} onChange={(event) => { setJobTitle(event.target.value); setValidation('') }} maxLength={160} placeholder="e.g. Data Engineer" autoComplete="organization-title" />
        <label htmlFor="market-geography">Geography <span>Optional</span></label>
        <input id="market-geography" name="geography" value={geography} onChange={(event) => { setGeography(event.target.value); setValidation('') }} maxLength={120} placeholder="e.g. Toronto, Canada" autoComplete="address-level2" />
        <label htmlFor="market-country">Market country</label>
        <select id="market-country" name="country_code" value={countryCode} onChange={(event) => setCountryCode(event.target.value as MarketCountryCode)}>
          {MARKET_COUNTRIES.map((country) => <option key={country.code} value={country.code}>{country.name}</option>)}
        </select>
        <label htmlFor="market-skills">Skill keywords <span>Optional · up to five</span></label>
        <textarea id="market-skills" name="skill_queries" rows={3} maxLength={600} value={skillQueries} onChange={(event) => { setSkillQueries(event.target.value); setValidation('') }} placeholder={'Python\nSQL'} />
        <div className="market-form-note"><Globe2 size={14} />Country uses the provider's supported market code; geography is passed as entered within that country.</div>
        <div className="market-form-note" role="status"><ShieldCheck size={14} />{providerStatusLoading ? 'Checking provider availability…' : providerStatus ? `${providerStatus.provider ?? 'No provider'} · ${providerStatus.message}` : 'Provider status could not be checked; you can still request an availability result.'}</div>
        {validation && <div className="market-message market-validation" role="alert"><AlertCircle size={16} />{validation}</div>}
        {error && <div className="market-message market-error" role="alert"><AlertCircle size={16} /><span>{error.message}</span><button type="button" onClick={() => setError(null)}>Dismiss</button></div>}
        <div className="market-submit-row"><span><ShieldCheck size={14} />No candidate evidence is sent to a market provider</span><button type="submit" className="button-primary" disabled={loading}>{loading ? <><LoaderCircle size={15} className="spin-icon" />Checking…</> : <><Sparkles size={15} />Check availability</>}</button></div>
      </form>

      <section className="market-results" aria-live="polite" aria-busy={loading}>
        {loading ? <div className="panel market-state"><div className="market-state-icon busy"><LoaderCircle className="spin-icon" size={22} /></div><h2>Checking market-data availability</h2><p>Contacting SkillFair’s configured market-data boundary. No external provider is called unless one is explicitly connected.</p><div className="market-progress"><i /></div></div> : error ? <div className="panel market-state"><div className="market-state-icon failed"><AlertCircle size={21} /></div><h2>{error.kind === 'validation' ? 'Check the role details' : error.kind === 'network' ? 'Market Intelligence is unreachable' : 'Market data could not be checked'}</h2><p>{error.message}</p><button type="button" className="button-secondary" onClick={() => setError(null)}>Return to form</button></div> : result ? <>
          <div className="market-result-summary panel"><div className="market-summary-top"><div><span className="eyebrow"><span className="eyebrow-line" /> MARKET DATA STATUS</span><h2>{result.job_title}</h2></div><span className={`market-status-pill ${result.data_availability}`}><i />{result.data_availability === 'available' ? result.freshness === 'stale' ? 'Stale verified data' : 'Verified data available' : 'Data unavailable'}</span></div>
            <div className="market-request-context">{result.geography ? <span><MapPin size={14} />{result.geography}</span> : <span><Globe2 size={14} />Geography not specified</span>}<span>{result.sources.length} {result.sources.length === 1 ? 'source' : 'sources'} · {result.insights.length} {result.insights.length === 1 ? 'insight' : 'insights'}</span></div>
            <p className="market-result-message">{result.message}</p>
          </div>
          {result.data_availability === 'available' && result.insights.length > 0 ? <>
            <div className="market-section-heading"><div><h2>Sourced insights</h2><p>Every insight below is linked to its returned source record.</p></div><span>{result.insights.length}</span></div>
            {result.insights.length > 1 && <section className="market-demand-chart" aria-label="Observed job-ad count comparison"><h3>Current indexed job-ad counts</h3>{result.insights.map((insight) => <div className="market-demand-row" key={insight.insight_id}><span>{insight.title}</span><progress max={Math.max(1, ...result.insights.map((item) => item.value ?? 0))} value={insight.value ?? 0} aria-label={`${insight.title}: ${insight.value ?? 'unavailable'} ${insight.unit ?? ''}`} /><b>{insight.value?.toLocaleString() ?? 'Unavailable'}</b></div>)}<p>Counts are provider search results for the same retrieval window; they are not a census, share of all jobs, or candidate skill evidence.</p></section>}
            <div className="market-insights">{result.insights.map((insight) => <InsightCard key={insight.insight_id} insight={insight} source={sourceById.get(insight.source_id)} />)}</div>
            <div className="market-section-heading market-sources-heading"><div><h2>Data provenance</h2><p>Provider attribution and timestamps returned by the service.</p></div></div>
            <div className="market-sources">{result.sources.map((source) => <MarketSource key={source.source_id} source={source} />)}</div>
          </> : <div className="panel market-unavailable"><div className="market-state-icon"><Database size={21} /></div><span className="market-empty-label">{result.sources.length ? 'NO SUPPORTED INSIGHTS RETURNED' : 'NO VERIFIED SOURCE CONNECTED'}</span><h2>Market insights aren’t available yet</h2><p>{result.sources.length ? 'A source was returned, but it provided no supported market insights for this role and geography.' : 'No verified market-data provider is connected, so SkillFair has no supported statistics to show for this request.'}</p><div className="market-future-note"><Sparkles size={15} /><span><b>Future opportunities</b>Verified data could later inform role requirements, relevant skills, and transferable-skills context. Those capabilities are not provided by this endpoint today.</span></div></div>}
        </> : <div className="panel market-state market-empty"><div className="market-state-icon"><BarChart3 size={22} /></div><span className="market-empty-label">READY WHEN YOU ARE</span><h2>Start with a role and location</h2><p>SkillFair will report whether sourced market data is available. It will not substitute illustrative figures when there is no provider.</p><div className="market-empty-footnote">Future SAP or other provider connections must supply verified source attribution and retrieval dates.</div></div>}
      </section>
    </div>
    <section className="market-capability-limits" aria-labelledby="market-capability-title"><h2 id="market-capability-title">What this source can and cannot show</h2><div><p><b>Current role and entered-keyword demand:</b> available only when a provider returns attributable search counts.</p><p><b>Emerging or declining skills:</b> unavailable; the connected search source does not return a historical skill-frequency series.</p><p><b>Related or transferable skills:</b> not inferred from co-occurring job-ad keywords. Use SkillFair's separately curated Transferable Skills workflow for evidence-aware exploration.</p><p><b>Candidate skill gaps:</b> not calculated from market counts. A gap requires comparison with the candidate's validated evidence and human review.</p></div></section>
    <p className="market-limitations">Provider data describes observations only; it does not establish causation, candidate suitability, or hiring outcomes. Skill frequency, transferable-skill relationships, and candidate skill gaps are not inferred from role vacancy counts.</p>
  </div>
}
