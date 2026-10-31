import test from 'node:test'
import assert from 'node:assert/strict'
import { impactMeasures } from '../src/data/impactMeasures.ts'
import {
  ImpactRequestTracker,
  impactMetricValue,
  impactRequestErrorMessage,
  impactSummaryBelongsTo,
  impactSummaryUrl,
  isAwaitingImpactObservations,
  refreshImpactSummaryAfterSubmission,
} from '../src/impactMeasurementState.ts'

test('impact dashboard contains only proposed measures with no outcome values', () => {
  assert.equal(impactMeasures.length, 4)
  assert.equal(new Set(impactMeasures.map(({ id }) => id)).size, impactMeasures.length)

  for (const measure of impactMeasures) {
    assert.equal(typeof measure.title, 'string')
    assert.ok(measure.title.trim())
    assert.ok(measure.purpose.trim())
    assert.ok(measure.dataNeeded.trim())
    assert.equal('value' in measure, false)
    assert.equal('result' in measure, false)
  }
})

test('impact dashboard distinguishes empty, insufficient, unavailable and measured zero values', () => {
  assert.equal(isAwaitingImpactObservations([{ sample_size: 0, data_availability: 'awaiting_data' }, { sample_size: 0, data_availability: 'awaiting_data' }]), true)
  assert.equal(isAwaitingImpactObservations([{ sample_size: 0, data_availability: 'unavailable' }]), false)
  assert.equal(isAwaitingImpactObservations([{ sample_size: 0, data_availability: 'awaiting_data' }, { sample_size: 1, data_availability: 'measured' }]), false)
  assert.equal(impactMetricValue(null, 'count', 'awaiting_data'), 'Awaiting data')
  assert.equal(impactMetricValue(null, 'rating_1_to_5', 'insufficient_sample'), 'Insufficient sample')
  assert.equal(impactMetricValue(null, 'rating_1_to_5', 'unavailable'), 'Unavailable')
  assert.equal(impactMetricValue(0, 'count', 'measured'), '0')
})

test('impact requests preserve applied date/category filters for refresh after submissions', () => {
  assert.equal(
    impactSummaryUrl({ category: 'recruiter_feedback', startDate: '2026-10-01', endDate: '2026-10-31' }),
    '/api/impact/summary?category=recruiter_feedback&start_date=2026-10-01&end_date=2026-10-31',
  )
  assert.equal(impactSummaryUrl({ category: 'all', startDate: '', endDate: '' }), '/api/impact/summary?category=all')
})

test('a successful feedback or baseline submission refreshes the summary, while a failure does not', async () => {
  let refreshCount = 0
  const reload = async () => { refreshCount += 1 }
  await refreshImpactSummaryAfterSubmission(true, reload)
  assert.equal(refreshCount, 1)
  await refreshImpactSummaryAfterSubmission(false, reload)
  assert.equal(refreshCount, 1)
})

test('stale summary responses cannot cross account changes or replace a newer refresh', () => {
  const tracker = new ImpactRequestTracker()
  const accountARequest = tracker.begin('account-a')
  const accountBRequest = tracker.begin('account-b')
  assert.equal(tracker.isCurrent(accountARequest, 'account-a'), false)
  assert.equal(tracker.isCurrent(accountBRequest, 'account-b'), true)

  const olderRefresh = tracker.begin('account-b')
  const newerRefresh = tracker.begin('account-b')
  assert.equal(tracker.isCurrent(olderRefresh, 'account-b'), false)
  assert.equal(tracker.isCurrent(newerRefresh, 'account-b'), true)
  tracker.invalidate()
  assert.equal(tracker.isCurrent(newerRefresh, 'account-b'), false)
})

test('impact dashboard handles authentication expiry and unavailable candidate feedback', () => {
  assert.match(impactRequestErrorMessage(401, 'anything', 'fallback'), /session has expired/i)
  assert.equal(impactRequestErrorMessage(503, null, 'summary unavailable'), 'summary unavailable')
  assert.equal(impactSummaryBelongsTo('account-a', 'account-a'), true)
  assert.equal(impactSummaryBelongsTo('account-a', 'account-b'), false)
  assert.equal(impactSummaryBelongsTo('', ''), false)
  assert.equal(impactMetricValue(null, 'rating_1_to_5', 'unavailable'), 'Unavailable')
})
