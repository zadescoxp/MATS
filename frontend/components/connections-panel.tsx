"use client";

import { Check, Trash2 } from "lucide-react";
import { FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ConnectionsState, SavedModel } from "@/lib/types";
import { cn } from "@/lib/cn";

const PROVIDERS = [
  { id: "openai", label: "OpenAI" },
  { id: "anthropic", label: "Anthropic" },
  { id: "gemini", label: "Google" },
  { id: "ollama", label: "Ollama" },
  { id: "baseten", label: "Baseten" },
  { id: "custom", label: "Custom endpoint" },
];

const EMPTY_DRAFT = {
  id: "",
  name: "",
  provider: "openai",
  model: "",
  base_url: "",
  api_key: "",
  supports_tools: true,
};

function providerLabel(provider: string) {
  return PROVIDERS.find((item) => item.id === provider)?.label ?? provider;
}

function needsBaseUrl(provider: string) {
  return provider === "ollama" || provider === "baseten" || provider === "custom";
}

export function ConnectionsPanel({
  connections,
  busy,
  onSaveBybit,
  onSaveModel,
  onDeleteModel,
  onSelectModel,
}: {
  connections: ConnectionsState | undefined;
  busy: string | null;
  onSaveBybit: (apiKey: string, apiSecret: string) => void;
  onSaveModel: (model: Record<string, unknown>) => void;
  onDeleteModel: (modelId: string) => void;
  onSelectModel: (modelId: string) => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const models = connections?.models ?? [];
  const activeId = connections?.active_model_id ?? "environment";
  const bybitHint = connections?.bybit_configured
    ? `${connections.bybit_source === "saved" ? "Saved on this machine" : "Using the environment keys"} · ${connections.bybit_api_key_hint ?? "key set"}`
    : "No Bybit key saved yet";

  function editModel(model: SavedModel) {
    if (model.builtin) return;
    setDraft({
      id: model.id,
      name: model.name,
      provider: model.provider,
      model: model.model,
      base_url: model.base_url ?? "",
      api_key: "",
      supports_tools: model.supports_tools,
    });
  }

  function submitModel(event: FormEvent) {
    event.preventDefault();
    onSaveModel({
      id: draft.id || undefined,
      name: draft.name,
      provider: draft.provider,
      model: draft.model,
      base_url: draft.base_url || undefined,
      api_key: draft.api_key || undefined,
      supports_tools: draft.supports_tools,
    });
    setDraft(EMPTY_DRAFT);
  }

  return (
    <div className="space-y-8">
      <section>
        <h3 className="text-sm font-medium">Bybit</h3>
        <p className="mt-1 text-[11px] leading-5 text-muted">{bybitHint}. Leave a field blank to keep the current value.</p>
        <div className="mt-3 grid gap-3">
          <Input
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={connections?.bybit_api_key_hint ?? "API key"}
            aria-label="Bybit API key"
          />
          <Input
            type="password"
            autoComplete="off"
            value={apiSecret}
            onChange={(event) => setApiSecret(event.target.value)}
            placeholder={connections?.bybit_api_secret_hint ?? "API secret"}
            aria-label="Bybit API secret"
          />
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              className="rounded-full"
              disabled={busy === "save_bybit" || (!apiKey.trim() && !apiSecret.trim())}
              onClick={() => {
                onSaveBybit(apiKey.trim(), apiSecret.trim());
                setApiKey("");
                setApiSecret("");
              }}
            >
              Save keys
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="rounded-full"
              disabled={busy === "save_bybit" || connections?.bybit_source !== "saved"}
              onClick={() => onSaveBybit("", "")}
            >
              Use environment keys
            </Button>
          </div>
        </div>
      </section>

      <section>
        <h3 className="text-sm font-medium">Models</h3>
        <p className="mt-1 text-[11px] leading-5 text-muted">
          The selected model is what the agents call on the next run. Keys stay in the local API process.
        </p>
        <div className="mt-3 space-y-1">
          {models.map((model) => {
            const selected = model.id === activeId;
            return (
              <div key={model.id} className={cn("flex items-center gap-2 rounded-2xl px-2 py-2", selected && "bg-white/[0.04]")}>
                <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onSelectModel(model.id)}>
                  <span className="flex items-center gap-2 text-sm">
                    <Check className={cn("h-3.5 w-3.5", selected ? "text-accent" : "text-transparent")} />
                    <span className="truncate">{model.name}</span>
                  </span>
                  <span className="mt-0.5 block truncate pl-5 text-[11px] text-muted-foreground">
                    {providerLabel(model.provider)} · {model.model}
                    {model.api_key_hint ? ` · ${model.api_key_hint}` : ""}
                  </span>
                </button>
                {!model.builtin && (
                  <>
                    <button type="button" className="text-[11px] text-muted hover:text-foreground" onClick={() => editModel(model)}>
                      Edit
                    </button>
                    <button
                      type="button"
                      className="grid h-8 w-8 place-items-center rounded-full text-muted hover:bg-white/5 hover:text-negative"
                      aria-label={`Delete ${model.name}`}
                      onClick={() => onDeleteModel(model.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </>
                )}
              </div>
            );
          })}
          {!models.length && <p className="text-xs text-muted">No models saved yet.</p>}
        </div>

        <form className="mt-4 space-y-3" onSubmit={submitModel}>
          <p className="text-[11px] font-medium text-muted">{draft.id ? "Update model" : "Add a model"}</p>
          <Input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="Name" aria-label="Model name" required />
          <select
            value={draft.provider}
            onChange={(event) => setDraft({ ...draft, provider: event.target.value })}
            aria-label="Model provider"
            className="min-h-10 w-full rounded-xl border border-border bg-background px-3 text-sm outline-none"
          >
            {PROVIDERS.map((provider) => (
              <option key={provider.id} value={provider.id}>{provider.label}</option>
            ))}
          </select>
          <Input value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} placeholder="Model id" aria-label="Model id" required />
          {needsBaseUrl(draft.provider) && (
            <Input
              value={draft.base_url}
              onChange={(event) => setDraft({ ...draft, base_url: event.target.value })}
              placeholder={draft.provider === "ollama" ? "http://127.0.0.1:11434" : "https://api.example.com/v1"}
              aria-label="Model base URL"
            />
          )}
          <Input
            type="password"
            autoComplete="off"
            value={draft.api_key}
            onChange={(event) => setDraft({ ...draft, api_key: event.target.value })}
            placeholder={draft.id ? "Leave blank to keep the saved key" : draft.provider === "ollama" ? "API key, if the server requires one" : "API key"}
            aria-label="Model API key"
          />
          <label className="flex items-center gap-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={draft.supports_tools}
              onChange={(event) => setDraft({ ...draft, supports_tools: event.target.checked })}
            />
            Supports tool calls
          </label>
          <div className="flex gap-2">
            <Button type="submit" size="sm" className="rounded-full" disabled={busy === "save_model"}>
              {draft.id ? "Update model" : "Save model"}
            </Button>
            {draft.id && (
              <Button type="button" variant="ghost" size="sm" className="rounded-full" onClick={() => setDraft(EMPTY_DRAFT)}>
                Cancel
              </Button>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}
