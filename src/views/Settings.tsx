import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { cn, formatBytes, humanise } from "@/lib/utils";
import {
  useAgents,
  useAgentsFile,
  useDeleteKey,
  useDesignChecklist,
  useFirstRun,
  useGodotStatus,
  useNotifications,
  useProviders,
  useReloadAgents,
  useSaveAgentsFile,
  useSaveGitSettings,
  useSaveGodot,
  useSaveKey,
  useSettings,
  useSaveSettings,
  useSettingsStorage,
  useSystemInfo,
  useTelegramDiscoverChat,
  useTelegramTest,
  useTestProvider,
} from "@/lib/queries";
import { useStudio } from "@/lib/store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FieldRow, Input, Switch } from "@/components/ui/field";
import { EmptyState, Spinner } from "@/components/ui/feedback";
import { QuotaMeter } from "@/components/QuotaMeter";
import {
  IconAlert,
  IconCheck,
  IconChevronRight,
  IconExternal,
  IconFolder,
  IconKey,
  IconRefresh,
} from "@/components/Icons";

/**
 * Settings, in sections, because this screen holds everything a person might need to fix at 11pm:
 * a key that stopped working, the Godot path, git identity, the Telegram bot, and the agent file.
 *
 * Two deliberate choices. First, **keys are write-only from the UI**: a saved key is shown as its
 * last four characters and nothing else, and testing happens on the server so the key never has to
 * travel back to the window. Second, **the agent table is the same data as agents.yaml** - a user
 * who prefers an editor can paste the file in here, and the roster reloads without restarting the
 * app.
 */
const SECTIONS = [
  { id: "keys", label: "Models and keys" },
  { id: "agents", label: "Agents" },
  { id: "godot", label: "Godot" },
  { id: "git", label: "History and identity" },
  { id: "notifications", label: "Notifications" },
  { id: "design", label: "Design quality" },
  { id: "about", label: "About and storage" },
] as const;

export function Settings() {
  const navigate = useNavigate();
  const [section, setSection] = useState<(typeof SECTIONS)[number]["id"]>("keys");
  const { data: providers, isLoading } = useProviders();
  const { data: agents } = useAgents();
  const { data: firstRun } = useFirstRun();
  const { data: settings } = useSettings();
  const { data: system } = useSystemInfo();
  const { data: storage } = useSettingsStorage();
  const { data: godot } = useGodotStatus();
  const { data: agentsFile } = useAgentsFile();
  const { data: notifications } = useNotifications();
  const { data: checklist } = useDesignChecklist();
  const showToast = useStudio((state) => state.showToast);

  const saveKey = useSaveKey();
  const deleteKey = useDeleteKey();
  const testProvider = useTestProvider();
  const saveGodot = useSaveGodot();
  const saveGit = useSaveGitSettings();
  const saveSettings = useSaveSettings();
  const saveAgentsFile = useSaveAgentsFile();
  const reloadAgents = useReloadAgents();
  const telegramTest = useTelegramTest();
  const telegramChat = useTelegramDiscoverChat();

  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});
  const [godotPath, setGodotPath] = useState("");
  const [gitName, setGitName] = useState("");
  const [gitEmail, setGitEmail] = useState("");
  const [telegramToken, setTelegramToken] = useState("");
  const [telegramChatId, setTelegramChatId] = useState("");
  const [agentsText, setAgentsText] = useState("");
  const [agentsDirty, setAgentsDirty] = useState(false);

  useEffect(() => {
    if (godot?.configured) setGodotPath((current) => current || godot.configured);
  }, [godot]);

  useEffect(() => {
    const git = (settings?.config as { git?: { user_name?: string; user_email?: string } } | undefined)?.git;
    if (git) {
      setGitName((current) => current || git.user_name || "");
      setGitEmail((current) => current || git.user_email || "");
    }
  }, [settings]);

  useEffect(() => {
    if (agentsFile && !agentsDirty) setAgentsText(agentsFile.content);
  }, [agentsFile, agentsDirty]);

  useEffect(() => {
    const telegram = (notifications as { telegram?: { chat_id?: string } } | undefined)?.telegram;
    if (telegram?.chat_id) setTelegramChatId((current) => current || telegram.chat_id || "");
  }, [notifications]);

  const providerList = providers?.providers ?? [];
  const configured = providerList.filter((provider) => provider.configured);
  const missing = providerList.filter((provider) => provider.requires_key && !provider.configured);

  const report = (title: string, body: string, tone: "info" | "success" | "danger" = "success") =>
    showToast({ title, body, tone });

  return (
    <div className="flex h-full">
      <nav aria-label="Settings sections" className="w-56 shrink-0 overflow-y-auto border-r border-app-border p-3">
        {SECTIONS.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setSection(item.id)}
            aria-current={section === item.id}
            className={cn(
              "mb-1 flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors duration-fast",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
              section === item.id ? "bg-charcoal-800 text-canary-200" : "text-mint-200 hover:bg-surface-700",
            )}
          >
            <span className="truncate">{item.label}</span>
            {section === item.id ? <IconChevronRight size={14} className="ml-auto" /> : null}
          </button>
        ))}
      </nav>

      <div className="min-w-0 flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl">
          {section === "keys" ? (
            <>
              <h1 className="text-lg font-semibold text-mint-100">Models and keys</h1>
              <p className="mt-1 text-sm text-mint-200">
                The studio runs on free tiers. Keys are stored encrypted on this computer and are never shown again
                after saving - only the last four characters.
              </p>

              {isLoading ? (
                <Spinner className="mt-6" label="Reading providers" />
              ) : (
                <>
                  <section className="mt-6">
                    <h2 className="mb-2 text-xs uppercase tracking-wide text-muted">
                      Ready to use ({configured.length})
                    </h2>
                    <div className="space-y-3">
                      {configured.map((provider) => (
                        <div key={provider.id} className="rounded-lg border border-app-border bg-app-surface px-4 py-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm text-mint-100">{provider.name}</span>
                            {provider.free_forever ? <Badge tone="success">Free</Badge> : null}
                            <Badge
                              tone={
                                provider.last_test_status === "valid"
                                  ? "success"
                                  : provider.last_test_status === "invalid"
                                    ? "danger"
                                    : "muted"
                              }
                            >
                              {provider.last_test_status === "valid"
                                ? "Working"
                                : provider.last_test_status === "invalid"
                                  ? "Not accepted"
                                  : provider.last_test_status === "rate_limited"
                                    ? "Rate limited"
                                    : "Not tested"}
                            </Badge>
                            <span className="ml-auto font-mono text-xs text-muted">{provider.masked_key}</span>
                          </div>
                          <div className="mt-3">
                            <QuotaMeter provider={provider} compact />
                          </div>
                          <div className="mt-3 flex flex-wrap gap-2">
                            <Button
                              variant="secondary"
                              size="sm"
                              loading={testProvider.isPending}
                              onClick={() =>
                                testProvider.mutate(
                                  { provider_id: provider.id },
                                  {
                                    onSuccess: (result) =>
                                      report(
                                        `${provider.name}: ${result.status}`,
                                        result.detail,
                                        result.status === "valid" ? "success" : "info",
                                      ),
                                    onError: (failure: Error) => report("Test failed", failure.message, "danger"),
                                  },
                                )
                              }
                            >
                              <IconRefresh size={14} />
                              Test the connection
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() =>
                                deleteKey.mutate(provider.id, {
                                  onSuccess: () =>
                                    report(`${provider.name} removed`, "Its tasks fall back to the next model.", "info"),
                                })
                              }
                            >
                              Remove
                            </Button>
                          </div>
                        </div>
                      ))}
                      {configured.length === 0 ? (
                        <EmptyState
                          title="No keys yet"
                          detail="Add one below. Everything except the build itself works without any key at all."
                        />
                      ) : null}
                    </div>
                  </section>

                  <section className="mt-8">
                    <h2 className="mb-2 text-xs uppercase tracking-wide text-muted">
                      Available free tiers ({missing.length})
                    </h2>
                    <div className="space-y-3">
                      {missing.map((provider) => (
                        <div key={provider.id} className="rounded-lg border border-app-border bg-app-surface px-4 py-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm text-mint-100">{provider.name}</span>
                            {provider.free_forever ? <Badge tone="success">Free forever</Badge> : null}
                            {provider.one_time_credit ? <Badge tone="muted">One-time credit</Badge> : null}
                            {provider.substitution ? (
                              <Badge tone="warning">Substituted by {humanise(provider.substitution.substitute)}</Badge>
                            ) : null}
                          </div>
                          <p className="mt-1 text-xs text-muted">{provider.free_tier || provider.notes}</p>
                          <div className="mt-3 flex flex-wrap items-end gap-2">
                            <Input
                              type="password"
                              value={keyDrafts[provider.id] ?? ""}
                              onChange={(event) =>
                                setKeyDrafts((previous) => ({ ...previous, [provider.id]: event.target.value }))
                              }
                              placeholder="Paste the key"
                              aria-label={`${provider.name} key`}
                              className="w-64"
                            />
                            <Button
                              variant="primary"
                              size="sm"
                              loading={saveKey.isPending}
                              disabled={(keyDrafts[provider.id] ?? "").trim().length < 8}
                              onClick={() =>
                                saveKey.mutate(
                                  { provider_id: provider.id, api_key: (keyDrafts[provider.id] ?? "").trim() },
                                  {
                                    onSuccess: (result) => {
                                      setKeyDrafts((previous) => ({ ...previous, [provider.id]: "" }));
                                      report(
                                        "Key saved and tested",
                                        `${result.masked_key}${
                                          result.resumed_tasks?.length
                                            ? ` - ${result.resumed_tasks.length} paused task(s) restarted`
                                            : ""
                                        }`,
                                      );
                                    },
                                    onError: (failure: Error) => report("That key was not accepted", failure.message, "danger"),
                                  },
                                )
                              }
                            >
                              <IconKey size={14} />
                              Save and test
                            </Button>
                            {provider.signup_url ? (
                              <a
                                href={provider.signup_url}
                                target="_blank"
                                rel="noreferrer"
                                className="flex items-center gap-1 text-xs text-canary-200 underline decoration-canary-600 underline-offset-2 transition-colors duration-fast hover:text-canary-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                              >
                                Get a free key
                                <IconExternal size={12} />
                              </a>
                            ) : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                </>
              )}
            </>
          ) : null}

          {section === "agents" ? (
            <>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h1 className="text-lg font-semibold text-mint-100">Agents</h1>
                  <p className="mt-1 text-sm text-mint-200">
                    Twelve agents live in one file. Change a model, reorder a fallback chain, or add a thirteenth - no
                    code change and no restart.
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  loading={reloadAgents.isPending}
                  onClick={() =>
                    reloadAgents.mutate(undefined, {
                      onSuccess: (result) => report("Roster reloaded", `${result.agents} agents ready.`),
                      onError: (failure: Error) => report("Reload failed", failure.message, "danger"),
                    })
                  }
                >
                  <IconRefresh size={14} />
                  Reload
                </Button>
              </div>

              <div className="mt-6 overflow-x-auto rounded-lg border border-app-border">
                <table className="w-full text-left text-sm">
                  <thead className="bg-charcoal-800 text-xs uppercase tracking-wide text-muted">
                    <tr>
                      <th className="px-3 py-2">Agent</th>
                      <th className="px-3 py-2">Primary</th>
                      <th className="px-3 py-2">Fallback 1</th>
                      <th className="px-3 py-2">Fallback 2</th>
                      <th className="px-3 py-2">State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(agents?.agents ?? []).map((agent) => (
                      <tr key={agent.agent_id} className="border-t border-app-border">
                        <td className="px-3 py-2">
                          <button
                            type="button"
                            onClick={() => navigate(`/agents/${agent.agent_id}`)}
                            className="flex items-center gap-2 text-left text-mint-100 underline decoration-transparent underline-offset-2 transition-colors duration-fast hover:decoration-surface-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                          >
                            <span
                              aria-hidden
                              className="h-2 w-2 rounded-full"
                              style={{ backgroundColor: agent.color ?? agent.colour ?? "var(--slate-500)" }}
                            />
                            {agent.name}
                          </button>
                        </td>
                        {[0, 1, 2].map((slot) => {
                          const entry = agent.chain?.[slot];
                          return (
                            <td key={slot} className="px-3 py-2 text-xs text-muted">
                              {entry ? `${entry.provider} / ${entry.model}` : "-"}
                            </td>
                          );
                        })}
                        <td className="px-3 py-2">
                          <Badge tone={agent.status === "awaiting_key" ? "warning" : agent.enabled ? "success" : "muted"}>
                            {humanise(agent.status ?? (agent.enabled ? "ready" : "off"))}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <section className="mt-6">
                <h2 className="mb-2 flex items-center justify-between gap-2 text-xs uppercase tracking-wide text-muted">
                  <span>agents.yaml</span>
                  <span className="normal-case">{agentsFile?.path}</span>
                </h2>
                <textarea
                  value={agentsText}
                  onChange={(event) => {
                    setAgentsText(event.target.value);
                    setAgentsDirty(true);
                  }}
                  aria-label="Agents file"
                  spellCheck={false}
                  disabled={saveAgentsFile.isPending || reloadAgents.isPending}
                  className="h-80 w-full rounded-md border border-app-border bg-charcoal-800 p-3 font-mono text-xs text-mint-100 transition-colors duration-fast hover:border-surface-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50 disabled:cursor-not-allowed"
                />
                <div className="mt-2 flex items-center justify-between gap-3">
                  <p className="text-xs text-muted">{agentsFile?.note}</p>
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={!agentsDirty}
                    loading={saveAgentsFile.isPending}
                    onClick={() =>
                      saveAgentsFile.mutate(agentsText, {
                        onSuccess: () => {
                          setAgentsDirty(false);
                          report("Roster saved", "The new chains are in use now.");
                        },
                        onError: (failure: Error) => report("That file was not accepted", failure.message, "danger"),
                      })
                    }
                  >
                    Save and reload
                  </Button>
                </div>
              </section>
            </>
          ) : null}

          {section === "godot" ? (
            <>
              <h1 className="text-lg font-semibold text-mint-100">Godot</h1>
              <p className="mt-1 text-sm text-mint-200">
                PulseG Studio drives a Godot editor you install yourself, so the project stays a normal Godot project
                that opens in the editor like any other.
              </p>

              <section className="mt-6 rounded-lg border border-app-border bg-app-surface px-4 py-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={godot?.works ? "success" : "warning"}>
                    {godot?.works ? `Working - ${godot.version || "version unknown"}` : "Not set up yet"}
                  </Badge>
                  <span className="text-xs text-muted">{godot?.configured || "no path saved"}</span>
                </div>
                <p className="mt-2 text-xs text-muted">{godot?.message}</p>
                <div className="mt-4 flex flex-wrap items-end gap-2">
                  <div className="min-w-[280px] flex-1">
                    <FieldRow label="Path to the Godot executable">
                      <Input
                        value={godotPath}
                        onChange={(event) => setGodotPath(event.target.value)}
                        placeholder="C:\Program Files\Godot\Godot_v4.4-stable_win64.exe"
                        aria-label="Godot path"
                      />
                    </FieldRow>
                  </div>
                  <Button
                    variant="primary"
                    size="sm"
                    loading={saveGodot.isPending}
                    disabled={godotPath.trim().length < 4}
                    onClick={() =>
                      saveGodot.mutate(godotPath.trim(), {
                        onSuccess: (result) =>
                          report(
                            result.works ? "Godot is working" : "Path saved",
                            result.message,
                            result.works ? "success" : "info",
                          ),
                        onError: (failure: Error) => report("That path did not work", failure.message, "danger"),
                      })
                    }
                  >
                    <IconFolder size={14} />
                    Save the path
                  </Button>
                </div>
                {godot?.detected?.length ? (
                  <div className="mt-3">
                    <p className="text-xs text-muted">Also found on this computer:</p>
                    <ul className="mt-1 space-y-1">
                      {godot.detected.map((candidate) => (
                        <li key={candidate}>
                          <button
                            type="button"
                            onClick={() => setGodotPath(candidate)}
                            className="text-xs text-canary-200 underline decoration-canary-600 underline-offset-2 transition-colors duration-fast hover:text-canary-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                          >
                            {candidate}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </section>
            </>
          ) : null}

          {section === "git" ? (
            <>
              <h1 className="text-lg font-semibold text-mint-100">History and identity</h1>
              <p className="mt-1 text-sm text-mint-200">
                Approving a task saves a point in the project's history. These details are what appear against it.
              </p>
              <div className="mt-6 rounded-lg border border-app-border bg-app-surface px-4 py-4">
                <FieldRow label="Name">
                  <Input value={gitName} onChange={(event) => setGitName(event.target.value)} aria-label="Name" />
                </FieldRow>
                <FieldRow label="Email">
                  <Input value={gitEmail} onChange={(event) => setGitEmail(event.target.value)} aria-label="Email" />
                </FieldRow>
                <Switch
                  label="Keep one branch per phase"
                  detail="Each phase of the build gets its own line of history, so a finished phase stays readable."
                  checked={Boolean((settings?.config as { git?: { branch_per_phase?: boolean } })?.git?.branch_per_phase)}
                  onCheckedChange={(value) => saveSettings.mutate({ git: { branch_per_phase: value } })}
                />
                <Switch
                  label="Only save on approval"
                  detail="Leave this on. It is what makes every entry in the history a human decision."
                  checked={Boolean((settings?.config as { git?: { commit_on_approval_only?: boolean } })?.git?.commit_on_approval_only)}
                  onCheckedChange={(value) => saveSettings.mutate({ git: { commit_on_approval_only: value } })}
                />
                {firstRun?.git_available && !firstRun.git_identity.name ? (
                  <p className="mt-2 flex items-start gap-2 text-xs text-canary-200">
                    <IconAlert size={14} className="mt-1 shrink-0" />
                    No name and email are set on this computer. Add them here so the history has an author.
                  </p>
                ) : null}
                <div className="mt-4 flex justify-end">
                  <Button
                    variant="primary"
                    size="sm"
                    data-primary-action
                    loading={saveGit.isPending}
                    onClick={() =>
                      saveGit.mutate(
                        { user_name: gitName, user_email: gitEmail },
                        { onSuccess: () => report("Identity saved", "New saves will use this name and email.") },
                      )
                    }
                  >
                    Save
                  </Button>
                </div>
              </div>
            </>
          ) : null}

          {section === "notifications" ? (
            <>
              <h1 className="text-lg font-semibold text-mint-100">Notifications</h1>
              <p className="mt-1 text-sm text-mint-200">
                The studio tells you when a task needs your decision, when a run stalls, when every model in a chain has
                failed, and when a phase is finished.
              </p>

              <div className="mt-6 rounded-lg border border-app-border bg-app-surface px-4 py-4">
                <h2 className="text-sm font-medium text-mint-100">On this computer</h2>
                <Switch
                  label="Windows notifications"
                  detail={
                    (notifications as { windows_toasts?: { platform_supported?: boolean } } | undefined)?.windows_toasts
                      ?.platform_supported
                      ? "Shown as a normal Windows notification."
                      : "Only supported on Windows. This build runs elsewhere, so nothing will appear."
                  }
                  checked={notifications?.windows_toasts.enabled ?? false}
                  disabled={!notifications?.windows_toasts.platform_supported}
                  onCheckedChange={(value) => saveSettings.mutate({ notifications: { windows_toasts: { enabled: value } } })}
                />
              </div>

              <div className="mt-4 rounded-lg border border-app-border bg-app-surface px-4 py-4">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-sm font-medium text-mint-100">Telegram</h2>
                  <Badge tone={notifications?.telegram.ready ? "success" : notifications?.telegram.has_token ? "warning" : "muted"}>
                    {notifications?.telegram.ready ? "Ready" : notifications?.telegram.has_token ? "Token saved" : "Off"}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-muted">
                  Free and works on your phone. Create a bot with @BotFather, paste the token, then send your new bot any
                  message so the studio can find the chat.
                </p>
                <div className="mt-4 space-y-3">
                  <FieldRow label="Bot token" hint="From @BotFather in Telegram. Stored encrypted on this computer.">
                    <Input
                      type="password"
                      value={telegramToken}
                      onChange={(event) => setTelegramToken(event.target.value)}
                      placeholder={notifications?.telegram.has_token ? "A token is saved - paste a new one to replace it" : "123456:ABC-DEF..."}
                      aria-label="Telegram bot token"
                    />
                  </FieldRow>
                  <FieldRow label="Chat id" hint="Leave blank and press Find my chat after sending the bot a message.">
                    <Input
                      value={telegramChatId}
                      onChange={(event) => setTelegramChatId(event.target.value)}
                      placeholder="Not set"
                      aria-label="Telegram chat id"
                    />
                  </FieldRow>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={saveSettings.isPending}
                    disabled={telegramToken.trim().length < 8 && telegramChatId.trim().length < 4}
                    onClick={() =>
                      saveSettings.mutate(
                        {
                          notifications: {
                            telegram: {
                              ...(telegramToken.trim() ? { bot_token: telegramToken.trim() } : {}),
                              ...(telegramChatId.trim() ? { chat_id: telegramChatId.trim() } : {}),
                              enabled: true,
                            },
                          },
                        },
                        {
                          onSuccess: () => {
                            setTelegramToken("");
                            report("Telegram saved", "Press Send a test message to confirm it arrives.");
                          },
                          onError: (failure: Error) => report("Could not save that", failure.message, "danger"),
                        },
                      )
                    }
                  >
                    Save
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    loading={telegramChat.isPending}
                    onClick={() =>
                      telegramChat.mutate(undefined, {
                        onSuccess: (result) => {
                          setTelegramChatId(result.chat_id);
                          report("Found your chat", result.detail);
                        },
                        onError: (failure: Error) => report("No chat found yet", failure.message, "info"),
                      })
                    }
                  >
                    Find my chat
                  </Button>
                  <Button
                    variant="primary"
                    size="sm"
                    loading={telegramTest.isPending}
                    onClick={() =>
                      telegramTest.mutate(undefined, {
                        onSuccess: (result) =>
                          report(
                            result.ok ? "Test message sent" : "Telegram said no",
                            result.detail,
                            result.ok ? "success" : "danger",
                          ),
                        onError: (failure: Error) => report("Test failed", failure.message, "danger"),
                      })
                    }
                  >
                    Send a test message
                  </Button>
                </div>
              </div>
            </>
          ) : null}

          {section === "design" ? (
            <>
              <h1 className="text-lg font-semibold text-mint-100">Design quality</h1>
              <p className="mt-1 text-sm text-mint-200">
                The checks every screen is held to, and the palette the dashboard is built from.
              </p>
              <ul className="mt-6 space-y-2">
                {(checklist?.items ?? []).map((item) => (
                  <li key={item.id} className="flex items-start gap-3 rounded-md border border-app-border bg-app-surface px-4 py-3">
                    <IconCheck size={16} className="mt-1 shrink-0 text-teal-400" />
                    <span>
                      <span className="block text-sm text-mint-100">{item.label}</span>
                      <span className="block text-xs text-muted">Checked by {item.enforced_by}</span>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-4 text-xs text-muted">
                The palette comes from one generated file. Colours cannot be added anywhere else in the app, which is
                what keeps the contrast checks true as the interface grows. The audit result is written to
                project-log/DESIGN_TASTE_AUDIT.md.
              </p>
            </>
          ) : null}

          {section === "about" ? (
            <>
              <h1 className="text-lg font-semibold text-mint-100">About and storage</h1>
              <div className="mt-6 grid gap-3 sm:grid-cols-2">
                <div className="rounded-lg border border-app-border bg-app-surface px-4 py-3">
                  <p className="text-xs uppercase tracking-wide text-muted">Version</p>
                  <p className="mt-1 text-sm text-mint-100">
                    {system?.app.name} {system?.app.version}
                  </p>
                </div>
                <div className="rounded-lg border border-app-border bg-app-surface px-4 py-3">
                  <p className="text-xs uppercase tracking-wide text-muted">Studio home</p>
                  <p className="mt-1 truncate text-sm text-mint-100" title={storage?.studio_home.path}>
                    {storage?.studio_home.path}
                  </p>
                  <p className="text-xs text-muted">{formatBytes(storage?.studio_home.bytes ?? 0)}</p>
                </div>
                <div className="rounded-lg border border-app-border bg-app-surface px-4 py-3">
                  <p className="text-xs uppercase tracking-wide text-muted">Projects folder</p>
                  <p className="mt-1 truncate text-sm text-mint-100" title={storage?.projects_root.path}>
                    {storage?.projects_root.path}
                  </p>
                  <p className="text-xs text-muted">{formatBytes(storage?.projects_root.bytes ?? 0)}</p>
                </div>
                <div className="rounded-lg border border-app-border bg-app-surface px-4 py-3">
                  <p className="text-xs uppercase tracking-wide text-muted">Tools found</p>
                  <p className="mt-1 text-sm text-mint-100">
                    {Object.entries(system?.tools ?? {})
                      .filter(([, value]) => Boolean(value))
                      .map(([name]) => humanise(name))
                      .join(", ") || "Checking..."}
                  </p>
                </div>
              </div>

              <section className="mt-6">
                <h2 className="mb-2 text-xs uppercase tracking-wide text-muted">Projects on this computer</h2>
                <ul className="space-y-1">
                  {(storage?.projects ?? []).map((project) => (
                    <li key={project.path} className="flex items-center gap-3 text-xs">
                      <span className="min-w-0 flex-1 truncate text-mint-200" title={project.path}>
                        {project.name}
                      </span>
                      <span className="text-muted">{formatBytes(project.bytes)}</span>
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-xs text-muted">{storage?.note}</p>
              </section>

              <p className="mt-6 rounded-md border border-app-border bg-app-surface px-4 py-3 text-xs text-muted">
                Everything the studio knows is in plain files inside these folders. Deleting the index or the cache is
                always safe: it rebuilds from the project folders, which are the source of truth.
              </p>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
