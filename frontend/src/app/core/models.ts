/** Shapes returned by the ResumeForge API. */

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
}

export interface ResumeSection {
  id: string;
  title: string;
  entries: number;
  bullets: number;
  is_skills: boolean;
}

export interface Resume {
  id: string;
  name: string;
  template_profile: string;
  page_count: number;
  compiles: boolean;
  created_at: string;
}

export interface ResumeDetail extends Resume {
  tex_source: string;
  stats: { sections?: number; entries?: number; bullets?: number };
  warnings: string[];
  sections: ResumeSection[];
}

export type GenerationStatus = 'queued' | 'running' | 'completed' | 'failed';

export interface Generation {
  id: string;
  status: GenerationStatus;
  company: string;
  role: string;
  version_no: number;
  base_resume_name: string;
  page_count: number;
  coverage_score: number;
  created_at: string;
}

/** One word-level segment of a rewrite, for side-by-side rendering. */
export interface DiffSegment {
  type: 'equal' | 'added' | 'removed';
  text: string;
}

export interface BulletDiff {
  bullet_id: string;
  op: string;
  reason: string;
  before: string;
  after: string;
  segments: DiffSegment[];
  /** False when a guard refused the edit. Shown, never hidden. */
  applied: boolean;
  rejection: string;
}

export interface Reordering {
  op: string;
  target_id: string;
  reason: string;
  detail: string;
  before: string;
  after: string;
}

export interface ChangeReport {
  strategy: string;
  diffs: BulletDiff[];
  reorderings: Reordering[];
  rejected_count: number;
}

export interface CoverageItem {
  requirement: string;
  status: 'covered_prominent' | 'covered_buried' | 'covered_rephrased' | 'absent';
  evidence_bullet_ids: string[];
  resume_phrasing: string;
  note: string;
}

export interface GapReport {
  company: string;
  role: string;
  seniority: string;
  domain: string;
  coverage_score: number;
  missing_required: string[];
  missing_preferred: string[];
  counts: Record<string, number>;
  assessment: string;
}

export interface GenerationDetail extends Generation {
  tex_source: string;
  error: string;
  pdf_url: string | null;
  coverage: { items?: CoverageItem[] };
  change_report: ChangeReport;
  gap_report: GapReport;
}

export interface ProgressEvent {
  stage: string;
  message: string;
  percent: number;
  detail: Record<string, unknown>;
}

export interface GapRoadmapItem {
  skill: string;
  occurrences: number;
  required_count: number;
  preferred_count: number;
  companies: string[];
}

export interface GapRoadmap {
  total_applications: number;
  items: GapRoadmapItem[];
}
