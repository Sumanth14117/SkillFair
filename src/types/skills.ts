export type SkillEvidence = {
  skill_name: string
  evidence_type: 'explicit' | 'inferred'
  supporting_excerpt: string
  relevance_explanation: string
  validation_status: 'evidence_found' | 'inference_unverified'
}

export type SkillsDiscoveryResult = {
  analysis_mode: 'development' | 'ai'
  message: string
  extracted_characters: number
  extracted_text: string
  skills: SkillEvidence[]
}
