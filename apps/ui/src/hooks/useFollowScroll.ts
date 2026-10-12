import { useLayoutEffect, useRef, useState } from "react";

/** Follow output until the reader scrolls away; only this container is moved. */
export function useFollowScroll(identity: string | null, revisions: readonly unknown[]) {
  const ref = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const [paused, setPaused] = useState(false);
  function bottom() {
    const node = ref.current;
    if (node) node.scrollTop = Math.max(0, node.scrollHeight - node.clientHeight);
  }
  function resume() {
    following.current = true;
    setPaused(false);
    bottom();
  }
  useLayoutEffect(() => { resume(); }, [identity]);
  useLayoutEffect(() => { if (following.current) bottom(); }, revisions);
  return {
    ref, paused, resume,
    onWheel: (event: React.WheelEvent<HTMLDivElement>) => {
      if (event.deltaY < 0) { following.current = false; setPaused(true); }
    },
    onScroll: () => {
      const node = ref.current;
      if (!node) return;
      const atBottom = node.scrollHeight - node.clientHeight - node.scrollTop <= 2;
      following.current = atBottom;
      setPaused(!atBottom);
    },
  };
}
