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
export interface InterestBlock {
  requested: boolean; enabled: boolean; reason: string | null; source: string
  alpha: number | null; state_version: number | null; updated_at: string | null
  age_seconds: number | null; stale: boolean; half_life_minutes: number | null
  events: number | null; top_genres: { genre: string; weight: number }[]; rule: string
}
export interface AdaptiveSourceStat {
  algorithm: string; base_weight: number; affinity: number; adaptive_weight: number; candidates: number
}
export interface AdaptiveBlock {
  requested: boolean; enabled: boolean; reason: string | null; beta: number | null
  state_version: number | null; stale: boolean; rule: string; sources: AdaptiveSourceStat[]
}
export interface FusionResult {
  status: string; algorithm: string; model_version: string; method: string; rrf_k: number
  dataset_version: string; split_version: string; runtime_mode: string; subject_key: string
  generated_at: string; requested_limit: number; candidate_depth: number
  pool_size: number; delivered: number; filtered_seen: number; filtered_out_of_catalog: number
  weights: Record<string, number>; sources: FusionSourceStat[]; interest: InterestBlock
  adaptive: AdaptiveBlock
  items: { movie_id: string; rank: number; algorithm: string; model_version: string; score: number; recall_sources: string[]; recall_ranks: Record<string, number>; interest_match?: number | null; interest_boost?: number | null }[]
}
export interface AssistantToolCall {
  tool: string; arguments: Record<string, unknown> | null; ok: boolean; matched: number
  error: string | null; evidence?: Record<string, unknown> | null
}
export interface AssistantItem {
  movie_id: string; title: string; original_title: string; genres: string[]; year: number | null
  poster_path: string | null; source_tool?: string; rank?: number; score?: number | null
  model_version?: string; algorithm?: string; similarity?: number; cooccurrence?: number
  my_rating?: string; kind?: string
}
export interface AssistantOfflineMetrics {
  partition: string; users: number | null; empty_users: number | null
  precision_at_10: number | null; recall_at_10: number | null
  model_version: string | null; protocol: string
}
export interface AssistantMethod {
  tool: string; algorithm: string; algorithm_label: string
  model_version: string | null; offline_metrics: AssistantOfflineMetrics
}
export interface AssistantResult {
  status: string; subject_key: string; question: string; answer: string
  provider: string; model: string; rounds: number
  usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number }
  tool_calls: AssistantToolCall[]; methods?: AssistantMethod[]; items: AssistantItem[]
  unverified_mentions: string[]; warnings: string[]; guardrails: string[]; generated_at: string
}
export interface BehaviorStatus {
  producer: {
    state: string; enabled: boolean; bootstrap: string; topic: string; sent: number
    failed: number; skipped: number; last_sent_at: string | null; last_error: string | null
    last_skipped_reason: string | null; timeout_seconds: number
  }
  consumer: {
    status: string; state_path: string; state_version: number; subjects: string[]
    consumed?: number; applied?: number; events_applied?: number; events_path?: string
    group?: string; topic?: string; half_life_minutes?: number; alpha?: number
    offsets?: Record<string, number>; state_available?: boolean; max_age_seconds?: number
    enabled?: boolean; generated_at?: string
  }
}
