import { useState, useEffect, useCallback } from "react";

const FIFTEEN_MINUTES = 15 * 60 * 1000;

export function useData(fetcher, interval = FIFTEEN_MINUTES) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (force = false) => {
    try {
      setError(null);
      if (force) setRefreshing(true);
      const result = await fetcher(force);
      setData(result);
      setLastUpdated(new Date());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [fetcher]);

  useEffect(() => {
    load(false);
    const timer = setInterval(() => load(false), interval);
    return () => clearInterval(timer);
  }, [load, interval]);

  const refresh = useCallback(() => load(true), [load]);

  return { data, loading, error, lastUpdated, refresh, refreshing };
}
