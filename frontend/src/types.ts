export interface Account { account_id: string; subject_key: string; login_name: string }
export interface Movie {
  movie_id: string; movielens_title: string; title_zh: string | null; genres: string[]
  release_year?: number | null; poster_path: string | null; original_title?: string | null
  overview?: string | null; release_date?: string | null; runtime_minutes?: number | null
  original_language?: string | null; directors?: unknown[] | null; cast_top10?: unknown[] | null
  rating?: string | null; created_at?: string; updated_at?: string; timestamp_utc?: number
}
export interface Page<T> { items: T[]; total: number; page: number; per_page: number }
export interface HistoryUser { ml_user_id: string; evaluation_role: string; subject_key: string }
export interface Algorithm { algorithm: string; status: string }
export interface AlgorithmResult {
  status: string; algorithm: string; model_version: string | null; batch_id: string | null
  dataset_version: string; split_version: string; runtime_mode: string
  items: { movie_id: string; rank: number; algorithm: string; model_version: string; score: number | null; recall_sources?: string[]; recall_ranks?: Record<string, number> }[]
}
export interface FusionSourceStat {
  algorithm: string; weight: number; model_version: string
  candidate_count: number; contributed: number; best_rank: number | null
}
export interface FusionResult {
  status: string; algorithm: string; model_version: string; method: string; rrf_k: number
  dataset_version: string; split_version: string; runtime_mode: string; subject_key: string
  generated_at: string; requested_limit: number; candidate_depth: number
  pool_size: number; delivered: number; filtered_seen: number; filtered_out_of_catalog: number
  weights: Record<string, number>; sources: FusionSourceStat[]
  items: { movie_id: string; rank: number; algorithm: string; model_version: string; score: number; recall_sources: string[]; recall_ranks: Record<string, number> }[]
}
