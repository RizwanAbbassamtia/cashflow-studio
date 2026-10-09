import { KeyRound, Plus, X } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";

import { errorMessage } from "../../api/client";
import { useSettings, useUpdateSettings } from "../../api/settings";
import { KEY_NAME_PATTERN, KEY_PURPOSE, KNOWN_KEY_NAMES } from "../../types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { ConfirmDialog } from "../ui/Dialog";
import { Input } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { ErrorState } from "../ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../ui/Table";
import { useToast } from "../ui/Toast";

/**
 * API keys live in <app_data_dir>/.env on this computer. The browser only ever sees a
 * masked version; a value typed here is sent once and never read back or logged.
 */
export function ApiKeysCard() {
  const settings = useSettings();
  const update = useUpdateSettings();
  const { toast } = useToast();

  const [editing, setEditing] = useState<{ name: string; value: string } | null>(null);
  const [clearing, setClearing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [newValue, setNewValue] = useState("");
  const [newNameError, setNewNameError] = useState<string | null>(null);

  const names = useMemo(() => {
    const extra = Object.keys(settings.data?.keys ?? {})
      .filter((name) => !(KNOWN_KEY_NAMES as ReadonlyArray<string>).includes(name))
      .sort();
    return [...KNOWN_KEY_NAMES, ...extra];
  }, [settings.data]);

  const saveKey = async (name: string, value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return;
    try {
      await update.mutateAsync({ keys: { [name]: trimmed } });
      toast({ tone: "success", title: `${name} saved`, description: "Stored in .env on this computer." });
      setEditing(null);
      setAdding(false);
      setNewName("");
      setNewValue("");
    } catch (error) {
      toast({ tone: "error", title: `Could not save ${name}`, description: errorMessage(error) });
    }
  };

  const clearKey = async (name: string) => {
    try {
      await update.mutateAsync({ keys: { [name]: null } });
      toast({ tone: "success", title: `${name} cleared` });
    } catch (error) {
      toast({ tone: "error", title: `Could not clear ${name}`, description: errorMessage(error) });
    } finally {
      setClearing(null);
    }
  };

  const submitNew = (event: FormEvent) => {
    event.preventDefault();
    const name = newName.trim().toUpperCase();
    if (!KEY_NAME_PATTERN.test(name)) {
      setNewNameError("Use capital letters, numbers and underscores, starting with a letter. Example: MY_SERVICE_KEY");
      return;
    }
    setNewNameError(null);
    void saveKey(name, newValue);
  };

  return (
    <Card
      id="keys"
      title="API keys"
      description="Each key is stored in .env in your app data folder on this computer. It is shown masked and never copied to the shared folder."
      actions={
        <Button variant="secondary" size="sm" icon={<Plus />} onClick={() => setAdding(true)} disabled={adding}>
          Add another key
        </Button>
      }
      flush
    >
      {settings.isPending ? (
        <LoadingBlock label="Loading keys..." />
      ) : settings.isError ? (
        <ErrorState error={settings.error} title="Could not load the keys" onRetry={() => void settings.refetch()} />
      ) : (
        <>
          {adding ? (
            <form onSubmit={submitNew} className="flex flex-col gap-3 border-b border-line bg-surface-2/40 px-5 py-4">
              <div className="flex flex-wrap items-start gap-3">
                <div className="flex w-72 flex-col gap-1">
                  <Input
                    className="font-mono"
                    placeholder="KEY_NAME"
                    value={newName}
                    autoComplete="off"
                    spellCheck={false}
                    invalid={Boolean(newNameError)}
                    aria-label="Key name"
                    onChange={(event) => setNewName(event.target.value.toUpperCase())}
                  />
                  {newNameError ? <p className="text-xs text-fail">{newNameError}</p> : null}
                </div>
                <Input
                  type="password"
                  className="w-96 font-mono"
                  placeholder="Paste the key value"
                  value={newValue}
                  autoComplete="new-password"
                  spellCheck={false}
                  aria-label="Key value"
                  onChange={(event) => setNewValue(event.target.value)}
                />
                <Button type="submit" variant="primary" size="md" loading={update.isPending} disabled={!newName || !newValue}>
                  Add key
                </Button>
                <Button
                  variant="ghost"
                  size="md"
                  icon={<X />}
                  onClick={() => {
                    setAdding(false);
                    setNewName("");
                    setNewValue("");
                    setNewNameError(null);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </form>
          ) : null}

          <Table>
            <THead>
              <TR>
                <TH>Key</TH>
                <TH className="w-[260px]">Value</TH>
                <TH className="w-[110px]">Status</TH>
                <TH className="w-[220px] text-right">Actions</TH>
              </TR>
            </THead>
            <TBody>
              {names.map((name) => {
                const key = settings.data.keys[name];
                const isSet = Boolean(key?.set);
                const isEditing = editing?.name === name;
                return (
                  <TR key={name}>
                    <TD>
                      <div className="flex items-center gap-2">
                        <KeyRound className="size-4 text-ink-faint" aria-hidden />
                        <span className="font-mono text-[13px] text-ink">{name}</span>
                      </div>
                      {KEY_PURPOSE[name] ? <p className="mt-0.5 pl-6 text-xs text-ink-muted">{KEY_PURPOSE[name]}</p> : null}
                    </TD>
                    <TD>
                      {isEditing ? (
                        <form
                          className="flex items-center gap-2"
                          onSubmit={(event) => {
                            event.preventDefault();
                            void saveKey(name, editing.value);
                          }}
                        >
                          <Input
                            type="password"
                            className="font-mono"
                            placeholder="Paste the key value"
                            value={editing.value}
                            autoFocus
                            autoComplete="new-password"
                            spellCheck={false}
                            aria-label={`New value for ${name}`}
                            onChange={(event) => setEditing({ name, value: event.target.value })}
                          />
                          <Button type="submit" variant="primary" size="sm" loading={update.isPending} disabled={!editing.value.trim()}>
                            Save
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => setEditing(null)} aria-label="Cancel" className="px-2">
                            <X />
                          </Button>
                        </form>
                      ) : isSet ? (
                        <span className="font-mono text-[13px] text-ink-muted">{key?.masked || "set"}</span>
                      ) : (
                        <span className="text-[13px] text-ink-faint">Not set</span>
                      )}
                    </TD>
                    <TD>{isSet ? <Badge tone="ok" dot>Set</Badge> : <Badge tone="neutral">Not set</Badge>}</TD>
                    <TD className="text-right">
                      {isEditing ? null : (
                        <div className="inline-flex items-center gap-1">
                          <Button variant="secondary" size="sm" onClick={() => setEditing({ name, value: "" })}>
                            {isSet ? "Replace" : "Set"}
                          </Button>
                          {isSet ? (
                            <Button variant="ghost" size="sm" className="text-ink-muted hover:text-fail" onClick={() => setClearing(name)}>
                              Clear
                            </Button>
                          ) : null}
                        </div>
                      )}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </>
      )}

      <ConfirmDialog
        open={clearing !== null}
        title={`Clear ${clearing ?? ""}?`}
        description="The key is removed from .env on this computer. Tools that use it stop working until you set it again."
        confirmLabel="Clear key"
        tone="danger"
        loading={update.isPending}
        onConfirm={() => {
          if (clearing) void clearKey(clearing);
        }}
        onCancel={() => setClearing(null)}
      />
    </Card>
  );
}
