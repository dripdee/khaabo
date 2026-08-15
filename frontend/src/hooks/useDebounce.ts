import { useEffect, useState } from "react";

/**
 * Debounce a rapidly changing value (search input).
 *
 * 250 ms is short enough to feel live and long enough to avoid a request per
 * keystroke, which matters because suggest hits the database on every call.
 */
export function useDebounce<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);

  return debounced;
}
