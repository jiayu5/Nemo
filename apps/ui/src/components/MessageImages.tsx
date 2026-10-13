import { useEffect, useState } from "react";
import { api } from "../api";
import type { ImageRef } from "../types";

function StoredImage({ sessionId, image, index }: { sessionId: string; image: ImageRef; index: number }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setUrl(null);
    setFailed(false);
    void api.imageBlob(sessionId, image.asset_id, controller.signal).then((blob) => {
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [sessionId, image.asset_id]);
  return failed ? <span role="status">Image {index + 1} unavailable</span>
    : url ? <a href={url} target="_blank" rel="noreferrer"><img src={url} alt={`Attached image ${index + 1}`} /></a>
    : <span role="status">Loading image {index + 1}…</span>;
}

export function MessageImages({ sessionId, images }: { sessionId: string; images: ImageRef[] }) {
  return <div className="message-images">{images.map((image, index) =>
    <StoredImage key={`${image.asset_id}-${index}`} sessionId={sessionId} image={image} index={index} />)}</div>;
}
