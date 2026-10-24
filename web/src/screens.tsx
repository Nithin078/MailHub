import { FormEvent, useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate, useOutletContext } from "react-router-dom";
import { api, ApiError, saveToken } from "./api";
import { useTheme, type ThemeChoice } from "./theme";

const LANES = [
  ["all", "All"],
  ["urgent", "Urgent"],
  ["normal", "Normal"],
  ["digest", "Digest"],
] as const;

export function Login() {
  const navigate = useNavigate();
  const { resolved, setChoice } = useTheme();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"demo" | "login" | "register">("demo");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const path = mode === "register" ? "/api/auth/register" : "/api/auth/login";
      const body = mode === "register" ? { email, password, name } : { email, password };
      const data = await api<{ access_token: string }>(path, { method: "POST", body: JSON.stringify(body) });
      saveToken(data.access_token);
      navigate("/inbox");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in");
    }
  }

  async function demo() {
    setError("");
    try {
      const data = await api<{ access_token: string }>("/api/auth/demo", { method: "POST" });
      saveToken(data.access_token);
      navigate("/inbox");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Demo is unavailable");
    }
  }

  return (
    <div className="login">
      <section className="login-art">
        <div className="kicker" style={{ letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--muted)", fontSize: 12 }}>
          Personal mail desk
        </div>
        <h2>The drive, the test link, and nothing else buzzing.</h2>
        <p className="muted" style={{ maxWidth: 520 }}>
          Rules run first. A bank debit stays silent and never spends a token. A fraud alert still reaches the phone.
          A campus mail and the HirePro link that follows it share one company timeline.
        </p>
        <div className="sample" aria-hidden>
          <Sample subject="Alert: suspicious transaction blocked" who="Kotak · College" kind="urgent" />
          <Sample subject="Accenture | complete your online assessment" who="HirePro · just now" kind="urgent" />
          <Sample subject="INR 540.00 debited via UPI" who="Kotak · silenced, no model call" kind="silent" />
        </div>
      </section>
      <section className="login-card">
        <div className="panel">
          <h2 style={{ fontFamily: "var(--serif)", fontWeight: 560, margin: "0 0 4px" }}>Inbox Agent</h2>
          <p className="muted">The sample inbox is already full. No Google account required.</p>
          <button className="btn" onClick={demo}>Open the demo inbox</button>
          <button className="btn ghost" type="button" onClick={() => setChoice(resolved === "dark" ? "light" : "dark")}>
            {resolved === "dark" ? "Light mode" : "Dark mode"}
          </button>
          <button className="btn ghost" onClick={() => setMode(mode === "login" ? "demo" : "login")}>
            {mode === "demo" ? "I have an account" : "Back"}
          </button>
          {mode !== "demo" && (
            <form onSubmit={submit} className="stack">
              {mode === "register" && <label>Name<input className="plain" value={name} onChange={(e) => setName(e.target.value)} /></label>}
              <label>Email<input className="plain" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
              <label>Password<input className="plain" type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
              <button className="btn" type="submit">{mode === "register" ? "Create account" : "Sign in"}</button>
              <button className="btn ghost" type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}>
                {mode === "login" ? "Create an account" : "Sign in instead"}
              </button>
            </form>
          )}
          {error && <div className="banner">{error}</div>}
          <p className="faint">Demo signs in as demo@inboxagent.local. Password is demopass if you want the form.</p>
        </div>
      </section>
    </div>
  );
}

function Sample({ subject, who, kind }: { subject: string; who: string; kind: string }) {
  return (
    <div className={`row ${kind}`} style={{ cursor: "default" }}>
      <div className="sender">{who}</div>
      <div className="subject">{subject}</div>
    </div>
  );
}

export function Shell({ onLogout }: { onLogout: () => void }) {
  const { resolved, setChoice } = useTheme();
  const [me, setMe] = useState<any>(null);
  const [settings, setSettings] = useState<any>(null);
  const [counts, setCounts] = useState<any>({});
  const [accountId, setAccountId] = useState(localStorage.getItem("ia_account") || "");
  const [accounts, setAccounts] = useState<any[]>([]);
  const [toast, setToast] = useState("");

  async function refresh() {
    const [profile, cfg, feed, accts] = await Promise.all([
      api("/api/auth/me"),
      api("/api/settings"),
      api("/api/feed?lane=all&limit=1" + (accountId ? `&account_id=${accountId}` : "")),
      api("/api/accounts"),
    ]);
    setMe(profile);
    setSettings(cfg);
    setCounts(feed.counts || {});
    setAccounts(accts);
  }

  useEffect(() => {
    refresh().catch(() => undefined);
    const timer = setInterval(() => refresh().catch(() => undefined), 20000);
    return () => clearInterval(timer);
  }, [accountId]);

  function chooseAccount(id: string) {
    setAccountId(id);
    localStorage.setItem("ia_account", id);
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="kicker">Solara</div>
          <h1>Inbox</h1>
        </div>
        <nav className="nav">
          <Item to="/inbox" label="Inbox" count={counts.urgent} />
          <Item to="/urgent" label="Urgent" count={counts.urgent} />
          <Item to="/silenced" label="Silenced" count={counts.silent} />
          <Item to="/todo" label="To-do" />
          <Item to="/opportunities" label="Timelines" />
          <Item to="/rules" label="Rules" />
          <Item to="/folders" label="Folders" />
          <Item to="/search" label="Search" />
          <Item to="/groups" label="Groups" />
          <Item to="/senders" label="Senders" />
          <Item to="/accounts" label="Accounts" />
          <Item to="/usage" label="Tokens" />
          <Item to="/settings" label="Settings" />
        </nav>
        <div className="grow" />
        <div className="who">
          <strong>{me?.name || "Signed in"}</strong>
          {me?.email}
          <div className="actions" style={{ marginTop: 8 }}>
            <button className="btn ghost small" type="button" onClick={() => setChoice(resolved === "dark" ? "light" : "dark")}>
              {resolved === "dark" ? "Light mode" : "Dark mode"}
            </button>
            <button className="btn ghost small" onClick={onLogout}>Sign out</button>
          </div>
        </div>
      </aside>
      <section className="main">
        <Outlet context={{ accountId, accounts, chooseAccount, settings, refresh, setToast, toast }} />
      </section>
      {settings && <GroupingPrompt settings={settings} refresh={refresh} />}
      {toast && <div className="toast" onClick={() => setToast("")}>{toast}</div>}
    </div>
  );
}

function Item({ to, label, count }: { to: string; label: string; count?: number }) {
  return (
    <NavLink to={to} className={({ isActive }) => (isActive ? "active" : "")}>
      <span>{label}</span>
      {typeof count === "number" && <span className="count">{count}</span>}
    </NavLink>
  );
}

function GroupingPrompt({ settings, refresh }: { settings: any; refresh: () => Promise<void> }) {
  const state = settings.grouping_enabled;
  const dismissals = settings.grouping_prompt_dismissals || 0;
  if (state !== "unset" || dismissals >= 3) return null;
  async function answer(value: string | null) {
    if (value) await api("/api/settings", { method: "PUT", body: JSON.stringify({ grouping_enabled: value }) });
    else await api("/api/settings", { method: "PUT", body: JSON.stringify({ grouping_prompt_dismissals: dismissals + 1 }) });
    await refresh();
  }
  return (
    <div className="modal-back">
      <div className="modal">
        <h2 style={{ fontFamily: "var(--serif)", marginTop: 0 }}>Group similar emails together?</h2>
        <p className="muted">Grouping only changes how mail is displayed. It does not move, delete, or re-notify anything.</p>
        <div className="actions">
          <button className="btn" onClick={() => answer("yes")}>Yes</button>
          <button className="btn ghost" onClick={() => answer("no")}>No</button>
          <button className="btn ghost" onClick={() => answer(null)}>Not now</button>
        </div>
      </div>
    </div>
  );
}

function desk() {
  return useOutletContext<any>() || {};
}

export function Inbox({ lane, title }: { lane: string; title: string }) {
  const { accountId, accounts, chooseAccount, setToast } = desk();
  const [feed, setFeed] = useState<any>({ items: [], counts: {} });
  const [selected, setSelected] = useState<string | null>(null);
  const [showNormal, setShowNormal] = useState(lane !== "all");

  async function load() {
    const path = lane === "all" ? "all" : lane;
    const data = await api(`/api/feed?lane=${path}&limit=80${accountId ? `&accountId=${accountId}` : ""}`.replace("accountId", "account_id"));
    setFeed(data);
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, [lane, accountId]);

  const items = (feed.items || []).filter((item: any) => {
    if (lane !== "all" || showNormal) return true;
    if (item.type === "group") return true;
    return item.message?.priority === "urgent" || item.pinned;
  });

  async function simulate(kind: string) {
    const message = await api<any>(`/api/demo/simulate/${kind}`, { method: "POST" });
    await load();
    if (message.priority === "silent") setToast?.("Stored silently. No notification, and the model was not called.");
    else if (message.priority === "urgent") setToast?.("Urgent. A phone with this account would buzz now.");
    else setToast?.(`Filed as ${message.priority}. ${message.decision_reason || ""}`);
    if (message.priority !== "silent") setSelected(message.id);
  }

  return (
    <>
      <header className="topbar">
        <h2>{title}</h2>
        <div className="spacer" />
        <select className="plain" value={accountId || ""} onChange={(e) => chooseAccount?.(e.target.value)}>
          <option value="">All accounts</option>
          {(accounts || []).map((account: any) => (
            <option key={account.id} value={account.id}>{account.label || account.email}</option>
          ))}
        </select>
        <select className="plain" defaultValue="" onChange={(e) => { if (e.target.value) { simulate(e.target.value); e.target.value = ""; } }}>
          <option value="">Drop in a sample…</option>
          <option value="fraud">Fraud alert</option>
          <option value="bank">Bank debit</option>
          <option value="placement">Campus drive</option>
          <option value="assessment">Assessment link</option>
          <option value="recruiter">Recruiter</option>
        </select>
      </header>
      {lane === "all" && (
        <div className="lanes">
          <button className={showNormal ? "" : "on"} onClick={() => setShowNormal(false)}>Urgent only</button>
          <button className={showNormal ? "on" : ""} onClick={() => setShowNormal(true)}>Everything that notifies</button>
          <span className="faint" style={{ alignSelf: "center" }}>{feed.counts?.urgent || 0} urgent · {feed.counts?.silent || 0} silenced</span>
        </div>
      )}
      <div className="split">
        <div className="list">
          {items.length === 0 && <p className="muted" style={{ padding: 18 }}>Nothing in this lane.</p>}
          {items.map((item: any) =>
            item.type === "group" ? (
              <button key={item.id} className="row digest" onClick={() => (window.location.href = "/groups")}>
                <div className="sender">{item.group.count} similar</div>
                <div className="time">{item.group.urgent_count ? `${item.group.urgent_count} urgent` : "group"}</div>
                <div className="subject">{item.group.latest_subject}</div>
                <div className="snippet">{item.group.latest_sender}</div>
              </button>
            ) : (
              <button key={item.id} className={`row ${item.message.priority} ${selected === item.message.id ? "selected" : ""} ${item.message.is_read ? "" : "unread"}`} onClick={() => setSelected(item.message.id)}>
                <div className="sender">{item.message.sender_name || item.message.sender_email}</div>
                <div className="time">{when(item.message.received_at)}</div>
                <div className="subject">{item.message.subject}</div>
                <div className="snippet">
                  <span className="chip"><i className="dot" style={{ background: item.message.account_color }} />{item.message.account_label}</span>
                  {" "}{item.message.snippet}
                </div>
              </button>
            ),
          )}
        </div>
        <div className="detail">
          {selected ? <Detail id={selected} onChanged={load} /> : <p className="muted">Select a mail. The trace on the right says why it was urgent, silent, or left for you.</p>}
        </div>
      </div>
    </>
  );
}

function Detail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { resolved } = useTheme();
  const [mail, setMail] = useState<any>(null);
  const [note, setNote] = useState("");
  const [folders, setFolders] = useState<any[]>([]);
  const [error, setError] = useState("");

  async function load() {
    const data = await api(`/api/messages/${id}`);
    setMail(data);
    if (!data.is_read) {
      await api(`/api/messages/${id}/read`, { method: "POST" });
      onChanged();
    }
  }

  useEffect(() => {
    load().catch((err) => setError(err.message));
    api("/api/folders").then((data) => setFolders(data.folders || [])).catch(() => undefined);
  }, [id]);

  if (!mail) return <p className="muted">{error || "Opening…"}</p>;
  const ink = resolved === "dark" ? "#f3efe6" : "#1c1916";
  const link = resolved === "dark" ? "#a9c0ef" : "#243e73";
  const quiet = resolved === "dark" ? "#9a9186" : "#8d867c";
  const srcDoc = `<!doctype html><html><head><base target="_blank"><style>body{font-family:Figtree,sans-serif;color:${ink};background:transparent;margin:0} a{color:${link}} .blocked-image{color:${quiet};font-size:13px}</style></head><body>${mail.body_html || ""}</body></html>`;

  async function act(path: string, options?: RequestInit) {
    await api(path, { method: "POST", ...options });
    await load();
    onChanged();
  }

  return (
    <>
      <div className="meta">
        <span className="chip"><i className="dot" style={{ background: mail.account_color }} />{mail.account_label}</span>
        <span className={`chip ${mail.priority}`}>{mail.priority}</span>
        <span className="chip">{mail.category}</span>
        {mail.llm_called ? <span className="chip">model ran</span> : <span className="chip">model skipped · {mail.llm_skipped_reason || "rule"}</span>}
      </div>
      <h3>{mail.subject || "(no subject)"}</h3>
      <div className="muted">{mail.sender_name} · {mail.sender_email}</div>
      {mail.also_in?.length > 0 && <p className="faint">Also arrived at {mail.also_in.map((row: any) => row.account_email).join(", ")}. Notified once.</p>}
      <div className="reason">
        <strong>{mail.decision_reason}</strong>
        <div className="trace">
          {(mail.decision_trace || []).map((step: any, index: number) => (
            <div key={index}><b>{step.step}</b><span>{step.result} — {step.detail}</span></div>
          ))}
        </div>
      </div>
      <div className="actions">
        <button className="btn small ghost" onClick={() => act(`/api/messages/${id}/star?starred=${!mail.is_starred}`)}> {mail.is_starred ? "Unstar" : "Star"}</button>
        <button className="btn small ghost" onClick={() => act(`/api/messages/${id}/archive`)}>Archive</button>
        <button className="btn small ghost" onClick={() => act(`/api/messages/${id}/ignore-like-this`)}>Ignore mails like this</button>
        <button className="btn small ghost" onClick={() => act(`/api/messages/${id}/pin?pinned=${!mail.pinned}`)}>{mail.pinned ? "Unpin" : "Pin"}</button>
        {mail.list_unsubscribe && <button className="btn small ghost" onClick={async () => {
          const result = await api<any>(`/api/messages/${id}/unsubscribe`, { method: "POST" });
          if (result?.url) window.open(result.url, "_blank", "noopener");
        }}>Unsubscribe</button>}
        <select className="plain" defaultValue="" onChange={async (e) => {
          if (!e.target.value) return;
          await api(`/api/folders/${e.target.value}/file`, { method: "POST", body: JSON.stringify({ message_id: id }) });
          e.target.value = "";
          await load();
        }}>
          <option value="">File in folder…</option>
          {folders.filter((folder) => !folder.is_smart).map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}
        </select>
      </div>
      {mail.extraction?.summary && <p><strong>{mail.extraction.summary}</strong></p>}
      <div className="mail-body">
        <iframe sandbox="" srcDoc={srcDoc} title="Message" />
      </div>
      <form className="stack" style={{ marginTop: 12 }} onSubmit={async (event) => {
        event.preventDefault();
        await api(`/api/messages/${id}/notes`, { method: "POST", body: JSON.stringify({ body: note }) });
        setNote("");
        await load();
      }}>
        <label>Private note<textarea className="plain" value={note} onChange={(e) => setNote(e.target.value)} /></label>
        <button className="btn small" type="submit">Save note</button>
      </form>
      {(mail.notes || []).map((item: any) => <p key={item.id} className="faint">{item.body}</p>)}
      {(mail.thread || []).length > 1 && (
        <div style={{ marginTop: 16 }}>
          <h4>Thread</h4>
          {mail.thread.map((item: any) => <div key={item.id} className="faint">{item.sender_email} — {item.subject}</div>)}
        </div>
      )}
    </>
  );
}

export function Rules() {
  const [rules, setRules] = useState<any[]>([]);
  const [current, setCurrent] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);
  const [plain, setPlain] = useState("");
  const [ai, setAi] = useState<any>(null);

  async function load() {
    setRules(await api("/api/rules"));
    setAi(await api("/api/llm"));
  }
  useEffect(() => { load().catch(() => undefined); }, []);

  function blank() {
    setCurrent({
      name: "New rule",
      conditions: { all: [{ field: "sender_domain", op: "eq", value: "" }] },
      exceptions: null,
      action: { type: "store_silent" },
      enabled: true,
    });
    setPreview(null);
  }

  async function save() {
    if (current.id) await api(`/api/rules/${current.id}`, { method: "PATCH", body: JSON.stringify(current) });
    else await api("/api/rules", { method: "POST", body: JSON.stringify(current) });
    await load();
  }

  async function runPreview() {
    setPreview(await api("/api/rules/preview", { method: "POST", body: JSON.stringify({ conditions: current.conditions, exceptions: current.exceptions }) }));
  }

  return (
    <>
      <header className="topbar"><h2>Rules</h2><div className="spacer" /><button className="btn" onClick={blank}>New rule</button></header>
      <div className="page grid-2">
        <div className="stack">
          {rules.map((rule) => (
            <button key={rule.id} className={`rule ${current?.id === rule.id ? "on" : ""}`} onClick={() => { setCurrent(rule); setPreview(null); }}>
              <strong>{rule.name}</strong>
              <div className="faint">{rule.action?.type} · {rule.hit_count} hits · {rule.enabled ? "on" : "off"}</div>
            </button>
          ))}
        </div>
        <div className="card stack">
          {!current && <p className="muted">Pick a rule. The bank rule silences debits and lets a fraud alert through.</p>}
          {current && (
            <>
              <label>Name<input className="plain" value={current.name} onChange={(e) => setCurrent({ ...current, name: e.target.value })} /></label>
              <label>Action
                <select className="plain" value={current.action?.type || "store_silent"} onChange={(e) => setCurrent({ ...current, action: { ...current.action, type: e.target.value } })}>
                  {["store_silent", "ignore", "digest", "notify_normal", "notify_urgent", "send_to_llm"].map((type) => <option key={type}>{type}</option>)}
                </select>
              </label>
              <LeafEditor title="Match all of" node={current.conditions} onChange={(conditions) => setCurrent({ ...current, conditions })} />
              <LeafEditor title="Unless any of" node={current.exceptions || { any: [] }} onChange={(exceptions) => setCurrent({ ...current, exceptions })} />
              <div className="actions">
                <button className="btn" onClick={save}>Save</button>
                <button className="btn ghost" onClick={runPreview}>Preview last 500</button>
                {current.id && <button className="btn ghost" onClick={async () => { await api(`/api/rules/${current.id}`, { method: "DELETE" }); setCurrent(null); await load(); }}>Delete</button>}
              </div>
              {preview && (
                <div>
                  <strong>{preview.matched}</strong> of {preview.scanned} stored headers match.
                  <ul>{(preview.samples || []).map((sample: any) => <li key={sample.id}>{sample.sender} — {sample.subject}</li>)}</ul>
                </div>
              )}
            </>
          )}
          <div className={ai?.enabled ? "" : "greyed"}>
            <h3>Plain English</h3>
            <p className="faint">{ai?.enabled ? "The model converts this once. You confirm. After that it is ordinary code." : "Add an API key to enable"}</p>
            <textarea className="plain" value={plain} disabled={!ai?.enabled} onChange={(e) => setPlain(e.target.value)} placeholder="Silence Kotak debits unless the subject says fraud" />
            <button className="btn small" disabled={!ai?.enabled} onClick={async () => {
              const proposed = await api("/api/rules/plain-english", { method: "POST", body: JSON.stringify({ text: plain }) });
              setCurrent({ ...proposed, enabled: true, origin: "plain_english" });
            }}>Convert</button>
          </div>
        </div>
      </div>
    </>
  );
}

function LeafEditor({ title, node, onChange }: { title: string; node: any; onChange: (node: any) => void }) {
  const key = node?.all ? "all" : "any";
  const leaves = node?.[key] || [];
  function update(index: number, leaf: any) {
    const next = leaves.slice();
    next[index] = leaf;
    onChange({ [key]: next });
  }
  return (
    <div>
      <div className="faint">{title}</div>
      {leaves.map((leaf: any, index: number) => (
        <div key={index} className="actions">
          <select className="plain" value={leaf.field} onChange={(e) => update(index, { ...leaf, field: e.target.value })}>
            {["sender_domain", "sender_email", "subject", "body", "label", "header"].map((field) => <option key={field}>{field}</option>)}
          </select>
          <select className="plain" value={leaf.op} onChange={(e) => update(index, { ...leaf, op: e.target.value })}>
            {["eq", "contains", "regex", "in", "present"].map((op) => <option key={op}>{op}</option>)}
          </select>
          <input className="plain" value={Array.isArray(leaf.value) ? leaf.value.join(", ") : leaf.value || ""} onChange={(e) => update(index, { ...leaf, value: leaf.op === "in" ? e.target.value.split(",").map((part) => part.trim()) : e.target.value })} />
        </div>
      ))}
      <button className="btn small ghost" type="button" onClick={() => onChange({ [key]: [...leaves, { field: "subject", op: "contains", value: "" }] })}>Add condition</button>
    </div>
  );
}

export function Opportunities() {
  const [rows, setRows] = useState<any[]>([]);
  const [company, setCompany] = useState("");
  async function load() { setRows(await api("/api/opportunities")); }
  useEffect(() => { load().catch(() => undefined); }, []);
  return (
    <>
      <header className="topbar"><h2>Timelines</h2></header>
      <div className="page stack">
        <form className="actions" onSubmit={async (event) => {
          event.preventDefault();
          await api("/api/opportunities", { method: "POST", body: JSON.stringify({ company }) });
          setCompany("");
          await load();
        }}>
          <input className="plain" placeholder="Company name" value={company} onChange={(e) => setCompany(e.target.value)} />
          <button className="btn">Add timeline</button>
        </form>
        {rows.map((row) => (
          <article key={row.id} className="card">
            <h3>{row.company}</h3>
            <div className="faint">{row.status}{row.deadline ? ` · deadline ${row.deadline}` : ""} · aliases {(row.aliases || []).join(", ") || "none"}</div>
            <p>{row.notes}</p>
            <div className="timeline">
              {(row.events || []).map((event: any) => (
                <article key={event.id}>
                  <strong>{event.event_type}</strong>
                  <div>{event.details?.summary || event.details?.subject}</div>
                  <div className="faint">Added by {event.added_by}</div>
                </article>
              ))}
            </div>
          </article>
        ))}
      </div>
    </>
  );
}

export function Todos() {
  const [data, setData] = useState<any>({ messages: [], reminders: [] });
  useEffect(() => { api("/api/todos").then(setData).catch(() => undefined); }, []);
  return (
    <>
      <header className="topbar"><h2>To-do</h2></header>
      <div className="page grid-2">
        <div className="stack">
          {data.messages.map((mail: any) => (
            <div key={mail.id} className="card">
              <strong>{mail.subject}</strong>
              <div className="faint">{mail.sender_email} · {mail.category} · {mail.priority}</div>
            </div>
          ))}
        </div>
        <div className="stack">
          <h3>Reminders</h3>
          {data.reminders.map((row: any) => (
            <div key={row.id} className="card">
              <strong>{row.note}</strong>
              <div className="faint">{row.remind_at}</div>
              <button className="btn small ghost" onClick={async () => { await api(`/api/reminders/${row.id}/done`, { method: "POST" }); setData(await api("/api/todos")); }}>Done</button>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

export function Folders() {
  const [data, setData] = useState<any>({ folders: [] });
  const [name, setName] = useState("");
  const [open, setOpen] = useState<any>(null);
  async function load() { setData(await api("/api/folders")); }
  useEffect(() => { load().catch(() => undefined); }, []);
  const roots = data.folders.filter((folder: any) => !folder.parent_id);
  return (
    <>
      <header className="topbar"><h2>Folders</h2><span className="faint">Depth limit {data.max_depth}. Filing never changes Gmail.</span></header>
      <div className="page grid-2">
        <div>
          <form className="actions" onSubmit={async (event) => { event.preventDefault(); await api("/api/folders", { method: "POST", body: JSON.stringify({ name }) }); setName(""); await load(); }}>
            <input className="plain" value={name} placeholder="New folder" onChange={(e) => setName(e.target.value)} />
            <button className="btn">Create</button>
          </form>
          <div className="tree">
            {roots.map((folder: any) => <FolderNode key={folder.id} folder={folder} all={data.folders} onOpen={async (row) => setOpen(await api(`/api/folders/${row.id}/messages?include_subfolders=true`))} />)}
          </div>
        </div>
        <div className="stack">
          {open && <h3>{open.folder}</h3>}
          {(open?.messages || []).map((mail: any) => <div key={mail.id} className="card">{mail.subject}<div className="faint">{mail.sender_email}</div></div>)}
        </div>
      </div>
    </>
  );
}

function FolderNode({ folder, all, onOpen }: { folder: any; all: any[]; onOpen: (folder: any) => void }) {
  const children = all.filter((item) => item.parent_id === folder.id);
  return (
    <div style={{ marginLeft: 8 }}>
      <button className="folder" onClick={() => onOpen(folder)}>{folder.name} <span className="faint">{folder.unread || ""}</span></button>
      {children.map((child) => <FolderNode key={child.id} folder={child} all={all} onOpen={onOpen} />)}
    </div>
  );
}

export function SearchPage() {
  const [q, setQ] = useState("from:vtu.ac.in");
  const [data, setData] = useState<any>(null);
  const [hints, setHints] = useState<any>(null);
  useEffect(() => { api(`/api/search/suggestions?q=${encodeURIComponent(q)}`).then(setHints).catch(() => undefined); }, [q]);
  return (
    <>
      <header className="topbar"><h2>Search</h2></header>
      <div className="page stack">
        <form className="actions" onSubmit={async (event) => { event.preventDefault(); setData(await api("/api/search", { method: "POST", body: JSON.stringify({ text: q }) })); }}>
          <input className="plain" style={{ flex: 1 }} value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn">Search</button>
        </form>
        <div className="faint">Try <code>from:kotak.com -subject:suspicious</code>, <code>folder:Placements</code>, <code>priority:urgent</code>. Gmail operators are passed through; folder and priority stay local.</div>
        {hints && <div className="faint">Recent: {(hints.recent || []).join(" · ") || "none yet"}</div>}
        {data && <div className="card">Gmail query: <code>{data.gmail_q || "(none)"}</code> · {data.total} local matches</div>}
        {(data?.results || []).map((mail: any) => <div key={mail.id} className="card"><strong>{mail.subject}</strong><div className="faint">{mail.sender_email} · {mail.priority}</div></div>)}
      </div>
    </>
  );
}

export function Groups() {
  const [rows, setRows] = useState<any[]>([]);
  const [open, setOpen] = useState<any>(null);
  async function load() { setRows(await api("/api/groups")); }
  useEffect(() => { load().catch(() => undefined); }, []);
  return (
    <>
      <header className="topbar"><h2>Groups</h2></header>
      <div className="page">
        {rows.length === 0 && <p className="muted">Nothing is grouped until you say yes, and a stack appears only after three similar mails. Silenced bank alerts show up here and in Silenced, not in the main inbox.</p>}
        <div className="grid-2">
          <div className="stack">
            {rows.map((row) => (
              <button key={row.id} className="rule" onClick={async () => setOpen(await api(`/api/groups/${row.id}`))}>
                <strong>{row.title}</strong>
                <div className="faint">{row.count} mails · {row.unread_count} unread · {row.kind}</div>
              </button>
            ))}
          </div>
          <div className="stack">
            {(open?.messages || []).map((mail: any) => <div key={mail.id} className="card">{mail.subject}<div className="faint">{mail.priority}</div></div>)}
          </div>
        </div>
      </div>
    </>
  );
}

export function Accounts() {
  const [rows, setRows] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [senders, setSenders] = useState<any[]>([]);
  async function load() { setRows(await api("/api/accounts")); }
  useEffect(() => { load().catch(() => undefined); }, []);
  return (
    <>
      <header className="topbar">
        <h2>Accounts</h2>
        <div className="spacer" />
        <button className="btn" onClick={async () => {
          try {
            const data = await api<{ url: string }>("/api/accounts/connect/start", { method: "POST" });
            window.location.href = data.url;
          } catch (err) {
            setError(err instanceof ApiError ? err.message : "Google is not configured");
          }
        }}>Add Gmail</button>
      </header>
      <div className="page stack">
        {error && <div className="banner">{error}</div>}
        {rows.map((account) => (
          <article key={account.id} className="card">
            <strong>{account.label || account.email}</strong>
            <div>{account.email}</div>
            <div className="faint">{account.provider} · {account.status} · {account.unread} unread</div>
            {account.status === "blocked" && <div className="banner">This account may be blocked by its administrator (common for college or work accounts).</div>}
            {account.status === "needs_reconnect" && <div className="banner warn">Reconnect this account. If the Cloud project is still in Testing, Google expires the refresh token after 7 days.</div>}
            <div className="actions">
              <button className="btn small ghost" onClick={async () => setSenders(await api(`/api/accounts/${account.id}/senders`))}>Top senders, 30 days</button>
              {account.provider !== "demo" && <button className="btn small ghost" onClick={async () => { await api(`/api/accounts/${account.id}`, { method: "DELETE" }); await load(); }}>Remove</button>}
            </div>
          </article>
        ))}
        {senders.length > 0 && (
          <div className="card">
            <h3>Onboarding scan</h3>
            <p className="faint">Headers only. Nothing here is sent to a model.</p>
            {senders.map((sender) => (
              <div key={sender.email} className="actions">
                <span style={{ flex: 1 }}>{sender.email} · {sender.count}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

export function Senders() {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => { api("/api/senders").then(setRows).catch(() => undefined); }, []);
  return (
    <>
      <header className="topbar"><h2>Senders</h2></header>
      <div className="page">
        <table>
          <thead><tr><th>Sender</th><th>Count</th><th>Last</th><th></th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.email}>
                <td>{row.name}<div className="faint">{row.email}</div></td>
                <td>{row.count}</td>
                <td className="faint">{row.last ? when(row.last) : ""}</td>
                <td>
                  <button className="btn small ghost" onClick={async () => {
                    await api("/api/group-preferences", { method: "POST", body: JSON.stringify({ kind: "sender", value: row.email, mode: "never" }) });
                  }}>Never group</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function Usage() {
  const [data, setData] = useState<any>(null);
  const [stats, setStats] = useState<any>(null);
  useEffect(() => {
    api("/api/llm/usage").then(setData).catch(() => undefined);
    api("/api/stats").then(setStats).catch(() => undefined);
  }, []);
  const max = Math.max(1, ...(stats?.per_day || []).map((row: any) => row.count));
  return (
    <>
      <header className="topbar"><h2>Usage</h2></header>
      <div className="page grid-2">
        <div className="card">
          <h3>Tokens today</h3>
          <p style={{ fontFamily: "var(--serif)", fontSize: 42, margin: 0 }}>{data?.today || 0}</p>
          <div className="faint">Cap {data?.cap}. The demo model records zero tokens and never leaves this machine.</div>
        </div>
        <div className="card">
          <h3>Mail per day</h3>
          <div className="bars">
            {(stats?.per_day || []).map((row: any) => <span key={row.date} title={`${row.date}: ${row.count}`} style={{ height: `${(row.count / max) * 100}%` }} />)}
          </div>
          <div className="faint">{stats?.unread} unread · {stats?.stored} stored</div>
        </div>
      </div>
    </>
  );
}

export function Settings() {
  const { choice, setChoice } = useTheme();
  const [data, setData] = useState<any>(null);
  const [provider, setProvider] = useState("gemini");
  const [model, setModel] = useState("gemini-2.0-flash-lite");
  const [apiKey, setApiKey] = useState("");
  const [message, setMessage] = useState("");

  async function load() {
    const settings = await api("/api/settings");
    setData(settings);
  }
  useEffect(() => { load().catch(() => undefined); }, []);
  if (!data) return <p className="muted" style={{ padding: 20 }}>Loading settings…</p>;
  const quiet = data.quiet_hours || {};

  async function saveKey(body: any) {
    const result = await api("/api/llm", { method: "PUT", body: JSON.stringify(body) });
    setMessage(result.message || result.status);
    await load();
  }

  return (
    <>
      <header className="topbar"><h2>Settings</h2></header>
      <div className="page stack">
        <section className="card stack">
          <h3>Appearance</h3>
          <p className="muted">Light is the paper desk. Dark keeps the same ink, only inverted. Match device follows your system setting.</p>
          <div className="actions">
            {(["light", "dark", "system"] as ThemeChoice[]).map((option) => (
              <button key={option} className={choice === option ? "btn small" : "btn ghost small"} type="button" onClick={() => setChoice(option)}>
                {option === "system" ? "Match device" : option === "dark" ? "Dark" : "Light"}
              </button>
            ))}
          </div>
        </section>
        <section className="card stack">
          <h3>Model</h3>
          <p className="muted">There is no shared key. Without one, rules, search, folders, and notifications keep working. Model features show as off.</p>
          <div className="faint">Status: {data.ai?.status} {data.ai?.key_last4 ? `· key ····${data.ai.key_last4}` : ""}</div>
          {message && <div className="banner ok">{message}</div>}
          <label>Provider
            <select className="plain" value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="gemini">Gemini</option>
              <option value="openai">OpenAI-compatible</option>
              <option value="ollama">Ollama</option>
              <option value="mock">Demo model (this machine)</option>
            </select>
          </label>
          <label>Model id<input className="plain" value={model} onChange={(e) => setModel(e.target.value)} /></label>
          <label>API key<input className="plain" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="Never shown again" /></label>
          <div className="actions">
            <button className="btn" onClick={() => saveKey({ provider, model, api_key: apiKey || "demo" })}>Save and test</button>
            <button className="btn ghost" onClick={() => saveKey({ provider: "mock", model: "inbox-agent-demo", api_key: "demo" })}>Enable demo model</button>
            <button className="btn ghost" onClick={async () => { await api("/api/llm", { method: "DELETE" }); setMessage("Key removed. AI is off."); await load(); }}>Remove key</button>
          </div>
        </section>
        <section className="card stack">
          <h3>Quiet hours</h3>
          <label><span><input type="checkbox" checked={!!quiet.enabled} onChange={(e) => setData({ ...data, quiet_hours: { ...quiet, enabled: e.target.checked } })} /> Enabled</span></label>
          <div className="actions">
            <input className="plain" value={quiet.start || "23:00"} onChange={(e) => setData({ ...data, quiet_hours: { ...quiet, start: e.target.value } })} />
            <input className="plain" value={quiet.end || "07:00"} onChange={(e) => setData({ ...data, quiet_hours: { ...quiet, end: e.target.value } })} />
          </div>
          <label><span><input type="checkbox" checked={quiet.allow_urgent !== false} onChange={(e) => setData({ ...data, quiet_hours: { ...quiet, allow_urgent: e.target.checked } })} /> Urgent mail still notifies</span></label>
          <label><span><input type="checkbox" checked={!!data.focus_mode} onChange={(e) => setData({ ...data, focus_mode: e.target.checked })} /> Focus mode (urgent only)</span></label>
          <label><span><input type="checkbox" checked={!!data.store_body_text} onChange={(e) => setData({ ...data, store_body_text: e.target.checked })} /> Store message bodies on this server</span></label>
          <button className="btn" onClick={async () => {
            await api("/api/settings", { method: "PUT", body: JSON.stringify({ quiet_hours: data.quiet_hours, focus_mode: data.focus_mode, store_body_text: data.store_body_text, grouping_enabled: data.grouping_enabled }) });
            setMessage("Saved");
          }}>Save notification settings</button>
        </section>
        <section className="card">
          <h3>Grouping</h3>
          <p className="faint">Current answer: {data.grouping_enabled}. Turning it on groups the last 30 days. Turning it off hides groups.</p>
          <div className="actions">
            <button className="btn small" onClick={async () => { await api("/api/settings", { method: "PUT", body: JSON.stringify({ grouping_enabled: "yes" }) }); await load(); }}>On</button>
            <button className="btn small ghost" onClick={async () => { await api("/api/settings", { method: "PUT", body: JSON.stringify({ grouping_enabled: "no" }) }); await load(); }}>Off</button>
          </div>
        </section>
      </div>
    </>
  );
}

function when(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  const diff = Date.now() - date.getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 36) return `${hours}h`;
  return date.toLocaleDateString();
}
