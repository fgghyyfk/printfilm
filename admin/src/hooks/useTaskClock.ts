import { useEffect, useState } from "react";

/** Keep durations live between server refreshes without making network requests. */
export function useTaskClock(active: boolean): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return active ? now : Date.now();
}
