import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchProvFeatures,
  fetchProvStacks,
  provCheck,
  provSet,
  type ProvCheckResult,
  type ProvFeature,
  type ProvSetResult,
  type ProvStack,
} from '../api'

type Busy = 'check' | 'set' | null

export function Provisioning() {
  const [stacks, setStacks] = useState<ProvStack[]>([])
  const [features, setFeatures] = useState<ProvFeature[]>([])
  const [feature, setFeature] = useState('')
  const [customFlags, setCustomFlags] = useState('')
  const [stack, setStack] = useState('')
  const [tenant, setTenant] = useState('')
  const [busy, setBusy] = useState<Busy>(null)
  const [checkRes, setCheckRes] = useState<ProvCheckResult | null>(null)
  const [setRes, setSetRes] = useState<ProvSetResult | null>(null)
  const [pending, setPending] = useState<'1' | '0' | null>(null)

  useEffect(() => {
    Promise.all([fetchProvStacks(), fetchProvFeatures()])
      .then(([s, f]) => {
        setStacks(s)
        setFeatures(f)
        if (s.length && !stack) setStack(s[0].name)
        if (f.length && !feature) setFeature(f[0].key)
      })
      .catch((e) => console.error('provisioning init failed', e))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const selected = useMemo(() => stacks.find((s) => s.name === stack), [stacks, stack])
  const parsedCustomFlags = useMemo(
    () =>
      customFlags
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
    [customFlags],
  )
  const activeFeature = useMemo<ProvFeature | null>(() => {
    if (feature === 'custom') {
      return {
        key: 'custom',
        label: 'Custom',
        description: 'Custom flag list',
        flags: parsedCustomFlags,
      }
    }
    return features.find((f) => f.key === feature) ?? null
  }, [feature, features, parsedCustomFlags])
  const flags = activeFeature?.flags ?? []
  const ready =
    !!stack &&
    !!feature &&
    /^\d+$/.test(tenant.trim()) &&
    (feature !== 'custom' || parsedCustomFlags.length > 0)

  const onCheck = useCallback(async () => {
    if (!ready) return
    setBusy('check')
    setCheckRes(null)
    try {
      setCheckRes(await provCheck(stack, tenant.trim(), flags))
    } catch (e) {
      setCheckRes({ ok: false, error: String(e) })
    } finally {
      setBusy(null)
    }
  }, [ready, stack, tenant, flags])

  const onSet = useCallback(
    async (value: '1' | '0') => {
      setPending(null)
      setBusy('set')
      setSetRes(null)
      try {
        const res = await provSet(stack, tenant.trim(), value, flags)
        setSetRes(res)
        if (res.ok) {
          setBusy('check')
          try {
            setCheckRes(await provCheck(stack, tenant.trim(), flags))
          } catch {
            /* verify-read is best-effort */
          }
        }
      } catch (e) {
        setSetRes({ ok: false, error: String(e) })
      } finally {
        setBusy(null)
      }
    },
    [stack, tenant, flags],
  )

  const actionLabel = pending === '1' ? 'Enable' : 'Disable'

  return (
    <div className="view provisioning" data-testid="provisioning-root">
      <header className="view-header" data-testid="header">
        <div className="title-block">
          <h1 data-testid="title">Provisioning</h1>
          <p className="subtitle">
            Enable / disable feature-flag groups for a tenant via the in-cluster provisioner service
          </p>
        </div>
      </header>

      <div className="prov-card" data-testid="prov-card">
        <div className="prov-form">
          <label className="prov-field">
            <span className="prov-label">Feature</span>
            <select
              className="prov-select"
              data-testid="provisioning-feature-select"
              value={feature}
              onChange={(e) => {
                setFeature(e.target.value)
                setCheckRes(null)
                setSetRes(null)
              }}
            >
              <option value="" disabled>
                Select a feature…
              </option>
              {features.map((f) => (
                <option key={f.key} value={f.key}>
                  {f.label}
                </option>
              ))}
              <option value="custom">Custom</option>
            </select>
          </label>

          <label className="prov-field">
            <span className="prov-label">Stack</span>
            <select
              className="prov-select"
              data-testid="provisioning-stack-select"
              value={stack}
              onChange={(e) => {
                setStack(e.target.value)
                setCheckRes(null)
                setSetRes(null)
              }}
            >
              <option value="" disabled>
                Select a stack…
              </option>
              {stacks.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.displayName} — {s.env.toUpperCase()}
                </option>
              ))}
            </select>
          </label>

          <label className="prov-field">
            <span className="prov-label">Tenant ID</span>
            <input
              className="prov-input"
              data-testid="provisioning-tenant-input"
              type="text"
              inputMode="numeric"
              placeholder="e.g. 18320"
              value={tenant}
              onChange={(e) => setTenant(e.target.value.replace(/[^0-9]/g, ''))}
            />
          </label>

          <div className="prov-actions">
            <button
              className="prov-btn"
              data-testid="provisioning-check-button"
              onClick={onCheck}
              disabled={!ready || busy !== null}
            >
              {busy === 'check' ? 'Checking…' : 'Check status'}
            </button>
            <button
              className="prov-btn prov-enable"
              data-testid="provisioning-enable-button"
              onClick={() => setPending('1')}
              disabled={!ready || busy !== null}
            >
              Enable
            </button>
            <button
              className="prov-btn prov-disable"
              data-testid="provisioning-disable-button"
              onClick={() => setPending('0')}
              disabled={!ready || busy !== null}
            >
              Disable
            </button>
          </div>
        </div>

        {activeFeature && (
          <div className="prov-feature-info" data-testid="provisioning-feature-info">
            <strong>{activeFeature.label}</strong>
            <span className="prov-feature-desc">{activeFeature.description}</span>
            {feature === 'custom' ? (
              <div className="prov-custom-flags">
                <input
                  className="prov-input prov-custom-input"
                  data-testid="provisioning-custom-flags-input"
                  type="text"
                  placeholder="comma-separated flags, e.g. nplan5283_ai_security, nplan6445_aiguardrails_vpe"
                  value={customFlags}
                  onChange={(e) => {
                    setCustomFlags(e.target.value)
                    setCheckRes(null)
                    setSetRes(null)
                  }}
                />
                {parsedCustomFlags.length > 0 && (
                  <ul className="prov-flag-list">
                    {parsedCustomFlags.map((f) => (
                      <li key={f}>
                        <code>{f}</code>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ) : (
              <ul className="prov-flag-list">
                {activeFeature.flags.map((f) => (
                  <li key={f}>
                    <code>{f}</code>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {selected && (
          <p className="prov-context" data-testid="provisioning-context">
            Target: <strong>{selected.displayName}</strong> ({selected.env.toUpperCase()}) · tenant{' '}
            <strong>{tenant.trim() || '—'}</strong>
          </p>
        )}
      </div>

      {(checkRes || busy === 'check') && (
        <CheckResultPanel result={checkRes} loading={busy === 'check'} />
      )}
      {(setRes || busy === 'set') && (
        <SetResultPanel result={setRes} loading={busy === 'set'} />
      )}

      {pending && (
        <div className="modal-overlay" data-testid="provisioning-confirm-dialog" role="dialog">
          <div className="modal">
            <h2 data-testid="provisioning-confirm-title">
              {actionLabel} {activeFeature?.label ?? feature} on {selected?.displayName ?? stack}?
            </h2>
            <p className="modal-body">
              This will set the following {activeFeature?.flags.length ?? 0} flag
              {(activeFeature?.flags.length ?? 0) === 1 ? '' : 's'} to{' '}
              <strong>{pending}</strong> for tenant <strong>{tenant.trim()}</strong> on stack{' '}
              <strong>{selected?.displayName ?? stack}</strong> ({selected?.env ?? ''}):
            </p>
            <ul className="modal-flags">
              {activeFeature?.flags.map((f) => (
                <li key={f}>
                  <code>{f}</code>
                </li>
              ))}
            </ul>
            <div className="modal-actions">
              <button
                className="prov-btn"
                data-testid="provisioning-confirm-cancel"
                onClick={() => setPending(null)}
              >
                Cancel
              </button>
              <button
                className={`prov-btn ${pending === '1' ? 'prov-enable' : 'prov-disable'}`}
                data-testid="provisioning-confirm-button"
                onClick={() => onSet(pending)}
              >
                Confirm {actionLabel}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function FlagBadge({ enabled }: { enabled: boolean | null }) {
  const cls = enabled === true ? 'badge-on' : enabled === false ? 'badge-off' : 'badge-unknown'
  const text = enabled === true ? 'ENABLED' : enabled === false ? 'DISABLED' : 'NOT FOUND'
  return <span className={`prov-badge ${cls}`}>{text}</span>
}

function CheckResultPanel({
  result,
  loading,
}: {
  result: ProvCheckResult | null
  loading: boolean
}) {
  return (
    <div className="prov-result-card" data-testid="provisioning-check-result">
      <h2 className="prov-result-title">Current status</h2>
      {loading && <p className="prov-loading">Working…</p>}
      {!loading && result && (
        <>
          {result.ok ? (
            <div data-testid="provisioning-check-result-ok">
              {result.allEnabled ? (
                <p className="prov-all on">All flags enabled ✓</p>
              ) : (
                <p className="prov-all off">Not all flags enabled</p>
              )}
              <table className="prov-flag-table" data-testid="provisioning-check-table">
                <tbody>
                  {(result.flags ?? []).map((f) => (
                    <tr key={f.flag} data-testid={`provisioning-check-row-${f.flag}`}>
                      <td>
                        <code>{f.flag}</code>
                      </td>
                      <td>
                        <FlagBadge enabled={f.enabled} />
                      </td>
                      <td className="prov-value-cell">
                        {f.value === null ? '—' : String(f.value)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="prov-fail" data-testid="provisioning-check-result-fail">
              <p className="prov-error" data-testid="provisioning-error-output">
                {result.error || 'Check failed'}
              </p>
              {result.output && (
                <pre className="prov-output" data-testid="provisioning-check-output">
                  {result.output}
                </pre>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function SetResultPanel({
  result,
  loading,
}: {
  result: ProvSetResult | null
  loading: boolean
}) {
  return (
    <div className="prov-result-card" data-testid="provisioning-set-result">
      <h2 className="prov-result-title">Set result</h2>
      {loading && <p className="prov-loading">Working…</p>}
      {!loading && result && (
        <>
          {result.ok ? (
            <div data-testid="provisioning-set-result-ok">
              <p className="prov-message">
                {result.action} completed — {result.summary?.ok}/{result.summary?.total} flag
                {result.summary?.total === 1 ? '' : 's'} set.
                {result.verifiedAllMatched
                  ? ' Verified ✓'
                  : (result.verified ? ' Verification shows mismatch.' : '')}
              </p>
              {result.verified && (
                <table className="prov-flag-table" data-testid="provisioning-set-verified-table">
                  <tbody>
                  {result.verified.flags.map((f) => (
                    <tr key={f.flag} data-testid={`provisioning-set-verified-${f.flag}`}>
                      <td><code>{f.flag}</code></td>
                      <td><FlagBadge enabled={f.enabled} /></td>
                      <td className="prov-value-cell">{f.value === null ? '—' : String(f.value)}</td>
                    </tr>
                  ))}
                  </tbody>
                </table>
              )}
              {result.verifyError && (
                <p className="prov-warn" data-testid="provisioning-set-verifywarn">
                  Verification failed: {result.verifyError}
                </p>
              )}
            </div>
          ) : (
            <div className="prov-fail" data-testid="provisioning-set-result-fail">
              <p className="prov-error" data-testid="provisioning-error-output">
                {result.error
                  ? result.error
                  : `${result.action ?? 'Set'} partially failed — ${result.summary?.ok ?? 0}/${
                      result.summary?.total ?? 0
                    } flags set.`}
              </p>
              {result.results && result.results.some((r) => !r.ok) && (
                <table className="prov-flag-table" data-testid="provisioning-set-fail-table">
                  <tbody>
                  {result.results
                    .filter((r) => !r.ok)
                    .map((r) => (
                      <tr key={r.flag}>
                        <td><code>{r.flag}</code></td>
                        <td><span className="prov-badge badge-unknown">FAILED</span></td>
                        <td className="prov-value-cell">{r.error}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {result.output && (
                <pre className="prov-output" data-testid="provisioning-set-output">
                  {result.output}
                </pre>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
