import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { InstalledSkills, Session, SkillDetail } from "../types";

function errorText(reason: unknown) {
  return reason instanceof Error ? reason.message : "Could not access skills";
}

export function SkillsPanel({ session, onClose }: { session: Session | null; onClose: () => void }) {
  const sessionId = session?.session_id ?? null;
  const [installed, setInstalled] = useState<InstalledSkills | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    dialog.current?.showModal();
    let active = true;
    api.skills(sessionId).then(value => { if (active) setInstalled(value); })
      .catch(reason => { if (active) setError(errorText(reason)); });
    return () => { active = false; };
  }, [sessionId]);

  async function act(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  return <dialog className="memory-dialog skills-dialog" ref={dialog} onCancel={onClose} aria-label="Installed skills">
    <header><h2>Installed skills</h2><button type="button" onClick={onClose}>Close</button></header>
    {error && <p role="alert" className="error-banner">{error}</p>}
    {!installed && !error && <p role="status">Loading skills…</p>}
    {installed?.entries.length === 0 && <p>No skills installed yet.</p>}
    <div className="skills-list">{installed?.entries.map(skill => {
      const scope = skill.scope === "builtin" ? "Built-in" : skill.scope === "user" ? "All projects" : "This project";
      return <button className="skill-row" key={skill.id} type="button" disabled={busy}
        aria-label={`View ${skill.name}`} aria-expanded={detail?.id === skill.id}
        title={skill.scope === "project" && session ? `${scope}: ${session.workspace}` : scope}
        onClick={() => void act(async () => {
          setDetail(await api.skillDetail(skill.id, sessionId));
        })}>
        <span className="skill-icon" aria-hidden="true"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M14 3v6h6M8 13h8M8 17h5" />
        </svg></span>
        <span className="skill-copy"><span className="skill-name">{skill.name}</span>
          <span className="skill-description" title={skill.description}>{skill.description}</span>
        </span>
      </button>;
    })}</div>
    {!!installed?.diagnostics.length && <section><h3>Discovery issues</h3>
      <ul>{installed.diagnostics.map((issue, i) => <li key={i}>{issue}</li>)}</ul>
    </section>}
    {detail && <section aria-label="Skill details"><h3>{detail.name}</h3>
      <p className="skill-detail-description">{detail.description}</p>
      <pre className="skill-body">{detail.body}</pre>
      <button type="button" onClick={() => setDetail(null)}>Hide details</button>
    </section>}
  </dialog>;
}
