/**
 * Shared constants. Keep in sync with backend config where applicable.
 * - Refresh timeout should match backend refresh_stream_timeout_seconds (ms = seconds * 1000).
 * - SES limits should match backend ses_suppression_search_limit / ses_bulk_remove_max / ses_min_search_chars.
 */

/** SSE refresh-done stream timeout (ms). Match backend refresh_stream_timeout_seconds. */
export const REFRESH_STREAM_TIMEOUT_MS = 300000;

/** SES suppression search: max results returned. Match backend ses_suppression_search_limit. */
export const SES_SUPPRESSION_SEARCH_LIMIT = 20;

/** SES suppression: max addresses per bulk-remove request. Match backend ses_bulk_remove_max. */
export const SES_BULK_REMOVE_MAX = 20;

/** SES suppression search: minimum query length. Match backend ses_min_search_chars. */
export const SES_MIN_SEARCH_CHARS = 3;
