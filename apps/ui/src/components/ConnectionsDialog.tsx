import { useEffect, useRef } from "react";
import type { ProviderSettings } from "../types";
import { ProviderSettingsPanel } from "./ProviderSettingsPanel";

export function ConnectionsDialog({ settings, onSaved, onClose }: {
  settings: ProviderSettings | null;
  onSaved: () => Promise<ProviderSettings>;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog className="connections-dialog" ref={dialog} onCancel={onClose} aria-label="Connections">
    <div className="connections-dialog-heading"><h2>Connections</h2>
      <button type="button" onClick={onClose}>Close</button>
    </div>
    <ProviderSettingsPanel settings={settings} onSaved={onSaved} />
  </dialog>;
}
