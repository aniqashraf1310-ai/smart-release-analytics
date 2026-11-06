import React, { useEffect, useState } from 'react';
import { Ban, Download, Gauge, Pencil, Trash2, X } from 'lucide-react';
import { api } from '../lib/api';
import { CHANGE_TYPES, FIX_TYPE_OPTIONS, QA_STATUS_OPTIONS, TARGET_ENVS, fmtDateTime, pct } from '../lib/constants';
import { can } from '../lib/permissions';
import ApprovalPanel from './ApprovalPanel';
import { RiskSummary, WhatIfSimulator } from './RiskPanel';
import { Button, ErrorBox, Field, GovStatusBadge, Tabs, TierBadge, inputClass } from './ui';

const LOCKED = ['DEPLOYING', 'DEPLOYED', 'FAILED', 'ROLLED_BACK', 'CANCELLED'];
const TRACKING = ['qa_status', 'responsible_qa', 'progression', 'notes'];
const MATERIAL = ['app_name', 'version', 'target_date', 'ticket_id', 'fix_type', 'description', 'change_type',
    'target_env', 'patch_size_loc', 'files_changed', 'test_coverage', 'ticket_complexity',
    'module_failure_rate', 'has_db_migration', 'dependency_changes'];

const Row = ({ label, children }) => (
    <div>
        <dt className="text-xs font-medium text-neutral">{label}</dt>
        <dd className="font-semibold text-gray-800 break-words">{children}</dd>
    </div>
);

const ReleaseDetail = ({ releaseId, user, onClose, onChanged, onDeleted }) => {
    const [release, setRelease] = useState(null);
    const [tab, setTab] = useState('overview');
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState({});
    const [score, setScore] = useState(null);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);

    const load = () => api(`/api/releases/${releaseId}`).then(r => { setRelease(r); setDraft(r); }).catch(e => setError(e.message));
    useEffect(() => { load(); }, [releaseId]); // eslint-disable-line react-hooks/exhaustive-deps

    // Show mitigations for a stored prediction too.
    useEffect(() => {
        if (release?.risk_tier && !score) {
            api(`/api/releases/${release.id}/mitigations`).then(m => setScore(s => s || {
                probability: Number(release.risk_probability), tier: release.risk_tier,
                explanation: release.risk_explanation, mitigations: m.mitigations,
            })).catch(() => {});
        }
    }, [release, score]);

    const updated = (r) => { setRelease(r); setDraft(r); onChanged(r); };

    const act = async (fn) => {
        setBusy(true);
        setError(null);
        try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); }
    };

    const doScore = () => act(async () => {
        const res = await api(`/api/releases/${release.id}/score`, { method: 'POST' });
        setScore(res);
        updated(res.release);
    });

    const save = () => act(async () => {
        const changes = {};
        [...TRACKING, ...MATERIAL].forEach(k => { if (draft[k] !== release[k]) changes[k] = draft[k]; });
        if (Object.keys(changes).length === 0) { setEditing(false); return; }
        const material = Object.keys(changes).some(k => MATERIAL.includes(k));
        if (material && ['IN_REVIEW', 'APPROVED', 'REJECTED'].includes(release.gov_status) &&
            !window.confirm('Changing release content returns it to DRAFT and invalidates all signatures. Continue?')) return;
        const r = await api(`/api/releases/${release.id}`, { method: 'PATCH', body: changes });
        setScore(null);
        updated(r);
        setEditing(false);
    });

    const cancel = () => act(async () => {
        const reason = window.prompt('Reason for cancelling this release?');
        if (!reason) return;
        const r = await api(`/api/releases/${release.id}/cancel`, { method: 'POST', body: { reason } });
        updated(r.release);
    });

    const remove = () => act(async () => {
        if (!window.confirm('Permanently delete this draft?')) return;
        await api(`/api/releases/${release.id}`, { method: 'DELETE' });
        onDeleted(release.id);
    });

    const exportPdf = async () => {
        const node = document.getElementById('release-detail-body');
        if (!node) return;
        const [{ default: html2canvas }, { default: JsPDF }] = await Promise.all([import('html2canvas'), import('jspdf')]);
        const canvas = await html2canvas(node, { scale: 2 });
        const pdf = new JsPDF('p', 'mm', 'a4');
        const w = 210, h = (canvas.height * w) / canvas.width;
        let left = h, pos = 0;
        pdf.addImage(canvas.toDataURL('image/png'), 'PNG', 0, pos, w, h);
        left -= 297;
        while (left > 0) { pos -= 297; pdf.addPage(); pdf.addImage(canvas.toDataURL('image/png'), 'PNG', 0, pos, w, h); left -= 297; }
        pdf.save(`release-${release.ticket_id}-rev${release.revision}.pdf`);
    };

    const set = (k, v) => setDraft(d => ({ ...d, [k]: v }));
    const input = (k, type = 'text', props = {}) => (
        <input type={type} className={inputClass} value={draft[k] ?? ''} {...props}
            onChange={e => set(k, type === 'number' ? Number(e.target.value) : e.target.value)} />
    );
    const select = (k, options) => (
        <select className={inputClass} value={draft[k]} onChange={e => set(k, e.target.value)}>
            {options.map(o => <option key={o}>{o}</option>)}
        </select>
    );

    const canEdit = release && !LOCKED.includes(release.gov_status) &&
        can(user, 'release.edit');
    const canScore = release?.gov_status === 'DRAFT' && can(user, 'risk.score');

    return (
        <div className="fixed inset-0 bg-gray-900/70 flex items-center justify-center p-2 sm:p-4 z-50" onClick={onClose}>
            <div className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[94vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                {!release ? (
                    <div className="p-8 text-center text-neutral">{error || 'Loading…'}</div>
                ) : (
                    <div id="release-detail-body" className="p-6">
                        <div className="flex items-start justify-between gap-3 mb-2">
                            <div>
                                <h3 className="text-2xl font-bold text-gray-800">{release.app_name} <span className="font-normal text-neutral">{release.version}</span></h3>
                                <div className="flex flex-wrap items-center gap-2 mt-1 text-sm">
                                    <GovStatusBadge status={release.gov_status} />
                                    <TierBadge tier={release.risk_tier} probability={release.risk_probability} />
                                    <span className="font-mono text-xs bg-gray-100 px-1.5 py-0.5 rounded text-gray-600">{release.ticket_id}</span>
                                    <span className="text-xs text-neutral">rev {release.revision}</span>
                                </div>
                            </div>
                            <button onClick={onClose} className="p-2 rounded-lg hover:bg-gray-100" title="Close"><X className="w-5 h-5" /></button>
                        </div>

                        <Tabs active={tab} onChange={setTab} tabs={[
                            { id: 'overview', label: 'Overview' },
                            { id: 'risk', label: 'Risk analysis' },
                            { id: 'approvals', label: `Approvals${release.gov_status === 'IN_REVIEW' ? ' •' : ''}` },
                            { id: 'audit', label: `Audit trail (${release.audit_trail.length})` },
                        ]} />
                        <ErrorBox error={error} />

                        {tab === 'overview' && (
                            <div className="space-y-5">
                                {!editing ? (
                                    <>
                                        <dl className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm p-4 bg-gray-50 rounded-lg border">
                                            <Row label="QA status">{release.qa_status}</Row>
                                            <Row label="QA responsible">{release.responsible_qa}</Row>
                                            <Row label="Target date">{release.target_date}</Row>
                                            <Row label="Fix type">{release.fix_type}</Row>
                                            <Row label="Change type">{release.change_type}</Row>
                                            <Row label="Environment">{release.target_env}</Row>
                                            <Row label="Patch size">{release.patch_size_loc} LOC / {release.files_changed} files</Row>
                                            <Row label="Test coverage">{Number(release.test_coverage)}%</Row>
                                            <Row label="Complexity">{release.ticket_complexity} / 5</Row>
                                            <Row label="Module failure rate">{pct(release.module_failure_rate, 0)}</Row>
                                            <Row label="DB migration">{release.has_db_migration ? 'Yes' : 'No'}</Row>
                                            <Row label="Dependency changes">{release.dependency_changes}</Row>
                                            <Row label="Created by">{release.created_by_username}</Row>
                                            <Row label="Submitted by">{release.submitted_by_username || '—'}</Row>
                                            <Row label="Updated">{fmtDateTime(release.updated_at)}</Row>
                                        </dl>
                                        <div className="p-4 bg-gray-50 rounded-lg border">
                                            <p className="text-sm font-semibold text-primary mb-1">Release notes</p>
                                            <p className="text-sm text-gray-700 whitespace-pre-wrap">{release.description}</p>
                                            <p className="text-sm font-semibold text-primary mt-3 mb-1">Progression</p>
                                            <p className="text-sm text-gray-700 whitespace-pre-wrap">{release.progression || '—'}</p>
                                        </div>
                                    </>
                                ) : (
                                    <div className="space-y-4">
                                        <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2">
                                            QA status, responsible QA, progression and notes can change freely. Changing anything else creates a new revision that must be re-scored and re-signed.
                                        </p>
                                        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                                            <Field label="QA status">{select('qa_status', QA_STATUS_OPTIONS)}</Field>
                                            <Field label="Responsible QA">{input('responsible_qa')}</Field>
                                            <Field label="Target date">{input('target_date', 'date')}</Field>
                                            <Field label="Version">{input('version')}</Field>
                                            <Field label="Fix type">{select('fix_type', FIX_TYPE_OPTIONS)}</Field>
                                            <Field label="Change type">{select('change_type', CHANGE_TYPES)}</Field>
                                            <Field label="Environment">{select('target_env', TARGET_ENVS)}</Field>
                                            <Field label="Patch size (LOC)">{input('patch_size_loc', 'number', { min: 0 })}</Field>
                                            <Field label="Files changed">{input('files_changed', 'number', { min: 0 })}</Field>
                                            <Field label="Test coverage (%)">{input('test_coverage', 'number', { min: 0, max: 100, step: 0.1 })}</Field>
                                            <Field label="Complexity (1-5)">{input('ticket_complexity', 'number', { min: 1, max: 5 })}</Field>
                                            <Field label="Module failure rate">{input('module_failure_rate', 'number', { min: 0, max: 1, step: 0.01 })}</Field>
                                            <Field label="Dependency changes">{input('dependency_changes', 'number', { min: 0 })}</Field>
                                            <label className="flex items-center gap-2 text-sm mt-6">
                                                <input type="checkbox" checked={!!draft.has_db_migration} onChange={e => set('has_db_migration', e.target.checked)} /> DB migration
                                            </label>
                                        </div>
                                        <Field label="Release notes"><textarea rows={3} className={inputClass} value={draft.description} onChange={e => set('description', e.target.value)} /></Field>
                                        <Field label="Progression"><textarea rows={2} className={inputClass} value={draft.progression} onChange={e => set('progression', e.target.value)} /></Field>
                                    </div>
                                )}
                                <div className="flex flex-wrap gap-2 pt-3 border-t">
                                    {editing ? (
                                        <>
                                            <Button disabled={busy} onClick={save}>Save</Button>
                                            <Button variant="secondary" onClick={() => { setDraft(release); setEditing(false); }}>Cancel</Button>
                                        </>
                                    ) : (
                                        <>
                                            {canEdit && <Button variant="secondary" onClick={() => setEditing(true)}><Pencil className="w-4 h-4" /> Edit</Button>}
                                            <Button variant="secondary" onClick={exportPdf}><Download className="w-4 h-4" /> Export PDF</Button>
                                            {release.gov_status === 'DRAFT' && !release.submitted_by && release.approvals.length === 0 && (release.created_by === user.id || user.role === 'RELEASE_MANAGER') && (
                                                <Button variant="ghost" disabled={busy} onClick={remove}><Trash2 className="w-4 h-4" /> Delete draft</Button>
                                            )}
                                            {!LOCKED.includes(release.gov_status) && can(user, 'release.cancel') && (release.created_by === user.id || user.role === 'RELEASE_MANAGER') && (
                                                <Button variant="ghost" disabled={busy} onClick={cancel}><Ban className="w-4 h-4" /> Cancel release</Button>
                                            )}
                                        </>
                                    )}
                                </div>
                            </div>
                        )}

                        {tab === 'risk' && (
                            <div className="space-y-5">
                                {canScore && (
                                    <div className="flex flex-wrap items-center gap-3">
                                        <Button disabled={busy} onClick={doScore}><Gauge className="w-4 h-4" /> {release.risk_tier ? 'Re-score' : 'Record risk score'}</Button>
                                        <span className="text-xs text-neutral">The recorded score decides which sign-offs are required.</span>
                                    </div>
                                )}
                                {score ? <RiskSummary score={score} /> : <p className="text-sm text-neutral">No score recorded for revision {release.revision} yet.</p>}
                                {release.risk_tier && <WhatIfSimulator release={release} />}
                                {release.predictions.length > 0 && (
                                    <div>
                                        <h4 className="text-sm font-semibold text-gray-700 mb-2">Score history</h4>
                                        <table className="w-full text-xs">
                                            <thead className="text-left text-neutral"><tr><th className="py-1">Rev</th><th>Probability</th><th>Tier</th><th>Model</th><th>When</th></tr></thead>
                                            <tbody>
                                                {release.predictions.map(p => (
                                                    <tr key={p.id} className="border-t">
                                                        <td className="py-1">{p.release_revision}</td><td className="tabular-nums">{pct(p.probability)}</td>
                                                        <td><TierBadge tier={p.risk_tier} /></td><td className="font-mono">{p.model_version}</td><td>{fmtDateTime(p.created_at)}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                )}
                            </div>
                        )}

                        {tab === 'approvals' && <ApprovalPanel release={release} user={user} onChanged={updated} />}

                        {tab === 'audit' && (
                            <ol className="relative border-l border-gray-200 ml-2 space-y-3">
                                {release.audit_trail.map(a => (
                                    <li key={a.seq} className="ml-4">
                                        <div className="absolute w-2.5 h-2.5 bg-primary rounded-full -left-[5px] mt-1.5" />
                                        <div className="text-sm"><b>{a.action.replaceAll('_', ' ').toLowerCase()}</b> <span className="text-neutral">by {a.actor || 'system'}</span></div>
                                        <div className="text-xs text-neutral">#{a.seq} · {fmtDateTime(a.occurred_at)}</div>
                                        {a.details && Object.keys(a.details).length > 0 && (
                                            <pre className="text-xs bg-gray-50 rounded p-2 mt-1 overflow-x-auto">{JSON.stringify(a.details, null, 1)}</pre>
                                        )}
                                    </li>
                                ))}
                            </ol>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};

export default ReleaseDetail;
