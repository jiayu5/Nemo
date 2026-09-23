import { useEffect, useState } from "react";
import { api } from "../api";
import type { ProviderSettings, ProviderSettingsDraft, SecretAction, SettingsProvider } from "../types";

interface Props {
  settings: ProviderSettings | null;
  onSaved: () => Promise<ProviderSettings>;
}

type EditorMode = "create" | "edit";
type ModelEditorMode = "create" | "edit";

const blankDraft: ProviderSettingsDraft = {
  protocol: "openai_compatible",
  base_url: "",
  api_key_env: "",
  proxy_env: null,
  timeout_seconds: 60,
  model_name: "",
  model_id: "",
  tool_calling: true,
  make_default: true,
  api_key: { action: "replace", value: "" },
  proxy: { action: "keep" },
};

function newDraft(): ProviderSettingsDraft {
  return { ...blankDraft, api_key: { action: "replace", value: "" }, proxy: { action: "keep" } };
}

function draftFor(provider: SettingsProvider, modelIndex = 0): ProviderSettingsDraft {
  const model = provider.models[modelIndex] ?? provider.models[0];
  return {
    protocol: provider.protocol as ProviderSettingsDraft["protocol"],
    base_url: provider.base_url,
    api_key_env: provider.api_key_env,
    proxy_env: provider.proxy_env,
    timeout_seconds: provider.timeout_seconds,
    model_name: model?.model_name ?? "",
    model_id: model?.model_id ?? "",
    tool_calling: model?.capabilities.includes("tool_calling") ?? true,
    make_default: model?.is_default ?? false,
    api_key: { action: "keep" },
    proxy: { action: "keep" },
  };
}

function safeDraft(draft: ProviderSettingsDraft): ProviderSettingsDraft {
  return {
    ...draft,
    base_url: draft.base_url.trim(),
    api_key_env: draft.api_key_env.trim(),
    proxy_env: draft.proxy_env?.trim() || null,
    model_name: draft.model_name.trim(),
    model_id: draft.model_id.trim(),
    api_key: draft.api_key.action === "replace" ? draft.api_key : { action: draft.api_key.action },
    proxy: draft.proxy.action === "replace" ? draft.proxy : { action: draft.proxy.action },
  };
}

export function ProviderSettingsPanel({ settings, onSaved }: Props) {
  const [mode, setMode] = useState<EditorMode>("edit");
  const [modelMode, setModelMode] = useState<ModelEditorMode>("edit");
  const [providerId, setProviderId] = useState("");
  const [draft, setDraft] = useState<ProviderSettingsDraft>(newDraft);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => {
    if (mode === "edit" && settings && settings.providers.length === 0) {
      setMode("create");
      return;
    }
    if (
      mode === "edit"
      && settings?.providers[0]
      && !settings.providers.some((provider) => provider.provider_id === providerId)
    ) {
      selectProvider(settings.providers[0]);
    }
  }, [settings, providerId, mode]);

  function selectProvider(provider: SettingsProvider, modelIndex = 0) {
    setMode("edit");
    setModelMode("edit");
    setProviderId(provider.provider_id);
    setDraft(draftFor(provider, modelIndex));
    setFeedback("");
    setConfirmingDelete(false);
  }

  function startNew() {
    setMode("create");
    setModelMode("create");
    setProviderId("");
    setDraft(newDraft());
    setFeedback("");
    setConfirmingDelete(false);
  }

  function startNewModel() {
    setModelMode("create");
    setDraft((current) => ({
      ...current,
      model_name: "",
      model_id: "",
      tool_calling: true,
      make_default: false,
      api_key: { action: "keep" },
      proxy: { action: "keep" },
    }));
    setFeedback("");
  }

  function updateProviderId(value: string) {
    setProviderId(value);
    setDraft((current) => {
      if (current.api_key_env) return current;
      const envName = value.trim().toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^([0-9])/, "_$1");
      return { ...current, api_key_env: envName ? `${envName}_API_KEY` : "" };
    });
  }

  function update<Key extends keyof ProviderSettingsDraft>(key: Key, value: ProviderSettingsDraft[Key]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function updateSecret(key: "api_key" | "proxy", action: SecretAction, value = "") {
    setDraft((current) => ({
      ...current,
      [key]: action === "replace" ? { action, value } : { action },
    }));
  }

  async function perform(action: "validate" | "save") {
    const normalizedProviderId = providerId.trim();
    if (!normalizedProviderId) {
      setFeedback("Enter a provider ID.");
      return;
    }
    if (
      modelMode === "create"
      && currentProvider?.models.some((model) => model.model_name === draft.model_name.trim())
    ) {
      setFeedback("This provider already has a model with that name.");
      return;
    }
    setBusy(true);
    setFeedback("");
    try {
      const payload = safeDraft(draft);
      const result = action === "validate"
        ? await api.validateProviderSettings(normalizedProviderId, payload)
        : await api.saveProviderSettings(normalizedProviderId, payload);
      if (action === "save") {
        const wasCreating = mode === "create";
        const wasAddingModel = mode === "edit" && modelMode === "create";
        const refreshed = await onSaved();
        const savedProvider = refreshed.providers.find(
          (provider) => provider.provider_id === normalizedProviderId,
        );
        if (savedProvider) {
          const savedModelIndex = savedProvider.models.findIndex(
            (model) => model.model_name === payload.model_name,
          );
          selectProvider(savedProvider, Math.max(savedModelIndex, 0));
        } else {
          setMode("edit");
          setProviderId(normalizedProviderId);
          setDraft((current) => ({ ...current, api_key: { action: "keep" }, proxy: { action: "keep" } }));
        }
        setFeedback(
          wasCreating
            ? "Provider added. New runs can use this model."
            : wasAddingModel
              ? "Model added to this provider."
              : "Saved. New runs will use this configuration.",
        );
      } else {
        setFeedback(`Valid. Will update ${result.writes.join(" and ")}.`);
      }
    } catch (reason) {
      setFeedback(reason instanceof Error ? reason.message : "Configuration failed");
    } finally {
      setBusy(false);
    }
  }

  async function testConnection() {
    setBusy(true);
    setFeedback("Connecting to the provider…");
    try {
      const result = await api.testProvider(providerId, draft.model_name || null);
      setFeedback(`Connection succeeded in ${Math.round(result.latency_ms)} ms.`);
    } catch (reason) {
      setFeedback(reason instanceof Error ? reason.message : "Connection test failed");
    } finally {
      setBusy(false);
    }
  }

  async function deleteProvider() {
    setBusy(true);
    setFeedback("");
    try {
      await api.deleteProviderSettings(providerId);
      const refreshed = await onSaved();
      if (refreshed.providers[0]) {
        selectProvider(refreshed.providers[0]);
      } else {
        startNew();
      }
      setConfirmingDelete(false);
    } catch (reason) {
      setFeedback(reason instanceof Error ? reason.message : "Could not delete provider");
    } finally {
      setBusy(false);
    }
  }

  const currentProvider = mode === "edit"
    ? settings?.providers.find((provider) => provider.provider_id === providerId)
    : undefined;
  const apiSource = currentProvider?.api_key_env === draft.api_key_env ? currentProvider.api_key_source : null;
  const proxySource = currentProvider?.proxy_env === draft.proxy_env ? currentProvider.proxy_source : null;

  return (
    <div className="settings-panel">
      <header className="settings-hero">
        <div>
          <strong>Model connections</strong>
          <p>Connect Nemo to any supported model endpoint.</p>
        </div>
        <button className="add-provider" type="button" onClick={startNew}>
          <span>+</span> Add provider
        </button>
      </header>

      <nav className="provider-strip" aria-label="Configured providers">
        {settings?.providers.map((provider) => (
          <button
            type="button"
            className={mode === "edit" && provider.provider_id === providerId ? "selected" : ""}
            key={provider.provider_id}
            onClick={() => selectProvider(provider)}
          >
            <i>{provider.provider_id.slice(0, 1).toUpperCase()}</i>
            <span>{provider.provider_id}</span>
            <small className={`source-${provider.api_key_source ?? "missing"}`}>
              {provider.api_key_source ? "ready" : "needs key"}
            </small>
          </button>
        ))}
        <button type="button" className={`provider-custom ${mode === "create" ? "selected" : ""}`} onClick={startNew}>
          <i>+</i><span>Custom</span><small>new</small>
        </button>
      </nav>

      <form className="settings-form" aria-label="Provider settings" onSubmit={(event) => {
        event.preventDefault();
        void perform("save");
      }}>
        <div className="settings-form-title">
          <div>
            <span>{mode === "create" ? "New connection" : "Connection settings"}</span>
            <h2>{mode === "create" ? "Add a model provider" : providerId}</h2>
          </div>
          {mode === "create" && settings?.providers.length ? (
            <button type="button" onClick={() => selectProvider(settings.providers[0])}>Cancel</button>
          ) : null}
        </div>

        <fieldset>
          <legend>Connection</legend>
          <div className="settings-row">
            <label>
              Provider ID
              <input
                value={providerId}
                disabled={mode === "edit"}
                onChange={(event) => updateProviderId(event.target.value)}
                placeholder="my-provider"
                autoFocus={mode === "create"}
              />
              <small>Used in model references and cannot change after creation.</small>
            </label>
            <label>
              API protocol
              <select value={draft.protocol} onChange={(event) => update("protocol", event.target.value as ProviderSettingsDraft["protocol"])}>
                <option value="openai_compatible">Chat Completions compatible</option>
                <option value="openai_responses" disabled>Responses API — coming later</option>
                <option value="anthropic_messages" disabled>Messages API — coming later</option>
              </select>
            </label>
          </div>
          <label>
            Endpoint
            <input value={draft.base_url} onChange={(event) => update("base_url", event.target.value)} placeholder="https://api.example.com/v1" />
          </label>
        </fieldset>

        <fieldset>
          <legend>Credentials</legend>
          <div className="credential-heading">
            <p>The secret stays in <code>~/.nemo/.env</code>, not in the model configuration.</p>
            <b className={`source-${apiSource ?? "missing"}`}>{apiSource ?? (mode === "create" ? "new" : "missing")}</b>
          </div>
          <div className="settings-row credential-row">
            <label>
              Environment variable
              <input value={draft.api_key_env} onChange={(event) => update("api_key_env", event.target.value)} placeholder="MY_PROVIDER_API_KEY" aria-label="API key variable" />
            </label>
            <label>
              Secret action
              <select aria-label="API key action" value={draft.api_key.action} disabled={apiSource === "environment"} onChange={(event) => updateSecret("api_key", event.target.value as SecretAction)}>
                <option value="keep">Use existing environment value</option>
                <option value="replace">Save a new value</option>
                <option value="delete">Delete file value</option>
              </select>
            </label>
          </div>
          {draft.api_key.action === "replace" && (
            <label>
              API key
              <input type="password" value={draft.api_key.value ?? ""} onChange={(event) => updateSecret("api_key", "replace", event.target.value)} placeholder="Paste a new key" autoComplete="new-password" aria-label="New API key" />
            </label>
          )}
        </fieldset>

        <fieldset>
          <legend>Model</legend>
          {currentProvider ? (
            <div className="model-catalog">
              <div className="model-catalog-heading">
                <span>{currentProvider.models.length} configured</span>
                <button type="button" onClick={startNewModel}>+ Add model</button>
              </div>
              <div className="model-tabs" aria-label="Provider models">
                {currentProvider.models.map((model, index) => (
                  <button
                    type="button"
                    className={modelMode === "edit" && model.model_name === draft.model_name ? "selected" : ""}
                    key={model.model_name}
                    onClick={() => selectProvider(currentProvider, index)}
                  >
                    <span>{model.model_name}</span>
                    <small>{model.model_id}</small>
                    {model.is_default ? <b>default</b> : null}
                  </button>
                ))}
                {modelMode === "create" ? (
                  <button type="button" className="selected model-new" onClick={startNewModel}>
                    <span>New model</span><small>Not saved yet</small>
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}
          <div className="model-editor-heading">
            <strong>{modelMode === "create" ? "Add model" : "Model settings"}</strong>
            <span>{modelMode === "create" && currentProvider ? `to ${providerId}` : ""}</span>
          </div>
          <div className="settings-row">
            <label>
              Model name
              <input
                value={draft.model_name}
                disabled={modelMode === "edit" && Boolean(currentProvider)}
                onChange={(event) => update("model_name", event.target.value)}
                placeholder="my-chat-model"
              />
              <small>The name shown in Nemo.</small>
            </label>
            <label>
              Remote model ID
              <input value={draft.model_id} onChange={(event) => update("model_id", event.target.value)} placeholder="model-v1" />
              <small>The exact model identifier sent to the API.</small>
            </label>
          </div>
          <div className="settings-options">
            <label className="settings-check">
              <input type="checkbox" checked={draft.tool_calling} onChange={(event) => update("tool_calling", event.target.checked)} />
              <span><b>Tool calling</b><small>Let the model use Nemo tools.</small></span>
            </label>
            <label className="settings-check">
              <input type="checkbox" checked={draft.make_default} onChange={(event) => update("make_default", event.target.checked)} />
              <span><b>Default model</b><small>Preselect this model for new sessions.</small></span>
            </label>
          </div>
        </fieldset>

        <details className="advanced-settings">
          <summary>Proxy and timeout</summary>
          <div className="advanced-content">
            <div className="settings-row">
              <label>
                Proxy environment variable
                <input value={draft.proxy_env ?? ""} onChange={(event) => update("proxy_env", event.target.value || null)} placeholder="NEMO_HTTPS_PROXY" aria-label="Proxy variable" />
              </label>
              <label>
                Proxy action
                <select aria-label="Proxy action" value={draft.proxy.action} disabled={!draft.proxy_env || proxySource === "environment"} onChange={(event) => updateSecret("proxy", event.target.value as SecretAction)}>
                  <option value="keep">Use existing value</option>
                  <option value="replace">Save a new value</option>
                  <option value="delete">Delete file value</option>
                </select>
              </label>
            </div>
            {draft.proxy.action === "replace" && (
              <label>
                Proxy URL
                <input type="password" value={draft.proxy.value ?? ""} onChange={(event) => updateSecret("proxy", "replace", event.target.value)} placeholder="http://127.0.0.1:7890" autoComplete="new-password" aria-label="New proxy URL" />
              </label>
            )}
            <label>
              Timeout seconds
              <input type="number" min="1" max="600" value={draft.timeout_seconds} onChange={(event) => update("timeout_seconds", Number(event.target.value))} />
            </label>
          </div>
        </details>

        {confirmingDelete && (
          <div className="delete-confirmation" role="alert">
            <div>
              <strong>Delete {providerId}?</strong>
              <p>Its models and their aliases or profiles will be removed. Saved secret values are retained.</p>
            </div>
            <div>
              <button type="button" disabled={busy} onClick={() => setConfirmingDelete(false)}>Cancel</button>
              <button className="danger" type="button" disabled={busy} onClick={() => void deleteProvider()}>Delete permanently</button>
            </div>
          </div>
        )}
        {feedback && <p className="settings-feedback" role="status">{feedback}</p>}
        <div className="settings-footer">
          <div className="connection-test-area">
            <button className="test-connection" type="button" disabled={busy || mode === "create"} onClick={() => void testConnection()}>Test connection</button>
            <small>Contacts the provider and may incur a charge.</small>
          </div>
          <div className="settings-actions">
            <button type="button" disabled={busy} onClick={() => void perform("validate")}>Validate</button>
            {mode === "edit" && currentProvider ? (
              <button className="danger" type="button" disabled={busy} onClick={() => setConfirmingDelete(true)}>Delete provider</button>
            ) : null}
            <button className="primary" type="submit" disabled={busy}>
              {mode === "create" ? "Confirm add" : modelMode === "create" ? "Add model" : "Save changes"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
