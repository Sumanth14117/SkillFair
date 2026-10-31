export type DurationTrendPoint = {
  sample_size: number
}

export type ImpactAvailability = 'awaiting_data' | 'insufficient_sample' | 'measured' | 'unavailable'

export function getDurationTrendEmptyState(points: DurationTrendPoint[], minimumSample: number, availability: ImpactAvailability) {
  const largestBucket = points.reduce((largest, point) => Math.max(largest, point.sample_size), 0)
  const overall = points.reduce((total, point) => total + point.sample_size, 0)
  const heading = availability === 'awaiting_data'
    ? 'Awaiting data'
    : availability === 'insufficient_sample'
      ? 'Insufficient sample per time bucket'
      : 'No chart data available'

  return {
    heading,
    detail: `Largest 5-minute bucket · ${largestBucket} of ${minimumSample} required; valid observations across selected period · ${overall}. No fabricated chart values are shown.`,
  }
}
