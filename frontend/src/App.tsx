import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, currentAccount, forgetAccount, listAccounts, switchAccount, type User } from './api'
import { Avatar, Wordmark } from './components/Ring'
import { Icon, Sheet, ToastProvider } from './components/ui'
import { AskView } from './views/Ask'
import { FindView } from './views/Find'
import { GoalsView } from './views/Goals'
import { ImportView } from './views/Import'
import { OptInPage } from './views/OptIn'
import { SignIn } from './views/SignIn'

export type Tab = 'import' | 'goals' | 'find' | 'ask'
export type AskDraft = { need: string; description: string; gap_id?: string; goal_id?: string; goal: string }

const TABS: { id: Tab; label: string }[] = [
  { id: 'import', label: 'Import' },
  { id: 'goals', label: 'Goals' },
  { id: 'find', label: 'Find' },
  { id: 'ask', label: 'Ask' },
]

function tabFromHash(): Tab {
  const h = window.location.hash.replace('#', '') as Tab
  return TABS.some((t) => t.id === h) ? h : 'import'
}

export default function App() {
  const optin = window.location.pathname.match(/^\/optin\/([\w-]+)/)
  return <ToastProvider>{optin ? <OptInPage token={optin[1]} /> : <Shell />}</ToastProvider>
}

function Shell() {
  const [account, setAccount] = useState(currentAccount())
  const [me, setMe] = useState<{ user: User; llm: boolean; model: string; provider: string; voice: boolean; plan: 'free' | 'pro'; discoverable: boolean } | null>(null)
  const [tab, setTab] = useState<Tab>(tabFromHash())
  const [draft, setDraft] = useState<AskDraft | null>(null)
  const [badge, setBadge] = useState(0)
  const [accountsOpen, setAccountsOpen] = useState(false)
  const [adding, setAdding] = useState(false)

  const go = useCallback((t: Tab) => {
    setTab(t)
    window.location.hash = t
    window.scrollTo({ top: 0 })
  }, [])

  useEffect(() => {
    const onHash = () => setTab(tabFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    if (!account) return
    setMe(null)
    api('/api/me')
      .then(setMe)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          forgetAccount(account.user.id)
          setAccount(currentAccount())
        }
      })
  }, [account])

  useEffect(() => {
    if (!account) return
    const poll = () =>
      Promise.all([api('/api/exchange/inbox'), api('/api/friends')])
        .then(([i, f]) => setBadge(i.requests.filter((r: any) => r.reply?.status === 'intro_requested').length + f.incoming.length))
        .catch(() => {})
    poll()
    const t = window.setInterval(poll, 10000)
    return () => window.clearInterval(t)
  }, [account])

  const refreshAccount = () => {
    setAdding(false)
    setAccountsOpen(false)
    setAccount(currentAccount())
  }

  if (!account || adding) return <SignIn onDone={refreshAccount} onCancel={account ? () => setAdding(false) : undefined} />
  if (!me) return <main className="signin"><Wordmark /></main>

  return (
    <div className="app">
      <header className="topbar">
        <Wordmark />
        <button className="account-button" onClick={() => setAccountsOpen(true)} aria-label={`Signed in as ${me.user.name}. Switch account`}>
          <Avatar name={me.user.name} size={28} />
          {me.user.name.split(' ')[0]}
        </button>
      </header>

      <main key={account.user.id}>
        {tab === 'import' && <ImportView go={go} />}
        {tab === 'find' && <FindView go={go} myName={me.user.name} plan={me.plan} onPlan={(plan) => setMe({ ...me, plan })} />}
        {tab === 'goals' && (
          <GoalsView
            go={go}
            ask={(d) => {
              setDraft(d)
              go('ask')
            }}
          />
        )}
        {tab === 'ask' && <AskView draft={draft} clearDraft={() => setDraft(null)} me={me.user} go={go} onInbox={setBadge} />}
      </main>

      <nav className="tabs" aria-label="Sections">
        <div className="tabs-inner">
          {TABS.map((t) => (
            <button key={t.id} className="tab" aria-current={tab === t.id ? 'page' : undefined} onClick={() => go(t.id)}>
              <Icon name={t.id} />
              {t.label}
              {t.id === 'ask' && badge > 0 && <span className="tab-badge">{badge}</span>}
            </button>
          ))}
        </div>
      </nav>

      {accountsOpen && (
        <Sheet onClose={() => setAccountsOpen(false)} label="Accounts">
          <h2>Accounts on this device</h2>
          <p className="small muted">
            Each account has its own graph. Switching is handy for demoing the Exchange between two people.
          </p>
          <div>
            {listAccounts().map((a) => (
              <button
                key={a.user.id}
                className="person-row"
                onClick={() => {
                  switchAccount(a.user.id)
                  refreshAccount()
                }}
              >
                <Avatar name={a.user.name} size={36} tie={a.user.id === account.user.id ? 'close' : 'known'} />
                <span className="grow">
                  <span className="person-name">{a.user.name}</span>
                  <span className="small muted" style={{ display: 'block' }}>
                    {a.user.handle}
                    {a.user.id === account.user.id ? ', current' : ''}
                  </span>
                </span>
              </button>
            ))}
          </div>
          <button className="btn btn-quiet" onClick={() => setAdding(true)}>
            Add another account
          </button>
          <div className="section-title">Search</div>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={me.discoverable}
              onChange={async (e) => {
                const s = await api('/api/settings', { method: 'PATCH', body: { discoverable: e.target.checked } })
                setMe({ ...me, ...s })
              }}
            />
            <span>
              Friends can find my contacts in search
              <span className="tiny faint" style={{ display: 'block' }}>
                They see name, title, company and LinkedIn link, as 2nd degree (3rd for their friends). Your notes and memos never leave your graph.
              </span>
            </span>
          </label>
          <div className="spread">
            <span className="small">
              Plan: <strong>{me.plan === 'pro' ? 'Pro' : 'Free'}</strong>
              <span className="tiny faint"> {me.plan === 'pro' ? '3rd degree search on' : '1st and 2nd degree search'}</span>
            </span>
            <button
              className="btn btn-ghost btn-small"
              onClick={async () => {
                const s = await api('/api/settings', { method: 'PATCH', body: { plan: me.plan === 'pro' ? 'free' : 'pro' } })
                setMe({ ...me, ...s })
              }}
            >
              {me.plan === 'pro' ? 'Back to Free' : 'Try Pro (demo)'}
            </button>
          </div>
          <p className="tiny faint">
            Model: {me.model}
            {me.provider === 'bedrock' ? ' on AWS Bedrock' : me.provider === 'mantle' ? ' on AWS Bedrock Mantle' : me.provider === 'openai' ? ' on OpenAI' : '. Add an AWS Bedrock or OpenAI key on the server for real extraction and ranking.'}
          </p>
        </Sheet>
      )}
    </div>
  )
}
