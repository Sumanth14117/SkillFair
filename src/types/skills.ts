export type SkillEvidence = {
  skill_name: string
  evidence_type: 'explicit' | 'inferred'
  detection_method: 'ai' | 'local'
  supporting_excerpt: string
  relevance_explanation: string
  validation_status: 'evidence_found' | 'inference_unverified'
}

export type SkillsDiscoveryResult = {
  analysis_id: string
  analysis_mode: 'development' | 'local' | 'ai'
  message: string
  extracted_characters: number
  skills: SkillEvidence[]
}
