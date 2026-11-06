import React, { useEffect, useState } from 'react';
import { CheckCircle2, Minus } from 'lucide-react';
import { api } from '../lib/api';
import { ROLE_LABELS, fmtDateTime } from '../lib/constants';
import { can } from '../lib/permissions';
import PermissionMatrix from './PermissionMatrix';
import { Button, Card, ErrorBox, TierBadge, inputClass } from './ui';

const TIERS = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];
const ROLES = ['DEVELOPER', 'QA_LEAD', 'RELEASE_MANAGER'];

const GovernanceView = ({ user }) => {
    const [data, setData] = useState(null);
    const [reviews, setReviews] = useState([]);
    const [findings, setFindings] = useState({});
    const [error, setError] = useState(null);

    const load = () => {
        api('/api/governance/policies').then(setData).catch(e => setError(e.message));
        api('/api/governance/incident-reviews').then(r => setReviews(r.reviews)).catch(e => setError(e.message));
    };
    useEffect(load, []);

    const complete = async (id) => {
        try {
            await api(`/api/governance/incident-reviews/${id}/complete`, { method: 'POST', body: { findings: findings[id] || '' } });
            load();
        } catch (e) { setError(e.message); }
    };

    const cell = (tier, role) => data?.policies.find(p => p.risk_tier === tier && p.role_code === role);

    return (
        <div className="space-y-6">
            <ErrorBox error={error} />
            <Card title="Roles and permissions" subtitle="What each of the three roles can do. Enforced by the API and again inside PostgreSQL.">
                <PermissionMatrix highlightRole={user.role} />
            </Card>
            <Card title="Risk-adaptive approval policy" subtitle="The predicted risk tier decides which signatures a release needs. Enforced inside PostgreSQL.">
                {data && (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="text-left text-neutral">
                                    <th className="py-2 pr-3">Role</th>
                                    {TIERS.map(t => <th key={t} className="py-2 px-2"><TierBadge tier={t} /></th>)}
                                </tr>
                            </thead>
                            <tbody>
                                {ROLES.map(role => (
                                    <tr key={role} className="border-t">
                                        <td className="py-2 pr-3 font-medium text-gray-800">{ROLE_LABELS[role]}</td>
                                        {TIERS.map(t => {
                                            const c = cell(t, role);
                                            return (
                                                <td key={t} className="py-2 px-2">
                                                    {c ? (
                                                        <span className="inline-flex items-center gap-1 text-gray-800">
                                                            <CheckCircle2 className="w-4 h-4 text-green-600" />
                                                            {c.min_approvals > 1 ? `${c.min_approvals} people` : 'required'}
                                                            {c.requires_risk_ack && <span className="text-xs text-amber-700">+ ack</span>}
                                                        </span>
                                                    ) : <Minus className="w-4 h-4 text-gray-300" />}
                                                </td>
                                            );
                                        })}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        <ul className="text-xs text-neutral mt-3 space-y-1 list-disc ml-5">
                            <li>The Developer signature is given by submitting; the submitter can never approve their own release.</li>
                            <li>Each person holds exactly one role and can give at most one signature per release revision, so "2 people" always means two different people.</li>
                            <li>"+ ack" means the approver must explicitly acknowledge the model's top risk drivers.</li>
                            {data.settings.map(s => <li key={s.key}>{s.description}: <b>{s.value}</b></li>)}
                        </ul>
                    </div>
                )}
            </Card>

            <Card title="Release state machine" subtitle="Every allowed transition. Anything else is rejected by a database trigger.">
                {data && (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead className="text-left text-neutral"><tr><th className="py-1">From</th><th>To</th><th>Who</th><th>When</th></tr></thead>
                            <tbody>
                                {data.transitions.map(t => (
                                    <tr key={`${t.from_status}-${t.to_status}`} className="border-t">
                                        <td className="py-1.5 font-mono text-xs">{t.from_status}</td>
                                        <td className="font-mono text-xs">{t.to_status}</td>
                                        <td className="text-xs">{t.actor}</td>
                                        <td className="text-xs text-gray-600">{t.description}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </Card>

            <Card title="Post-incident reviews" subtitle="Opened automatically for every break-glass approval.">
                {reviews.length === 0 ? <p className="text-sm text-neutral">No break-glass approvals so far.</p> : (
                    <ul className="space-y-3">
                        {reviews.map(r => (
                            <li key={r.id} className={`border rounded-lg p-3 text-sm ${r.overdue ? 'border-red-300 bg-red-50' : ''}`}>
                                <div className="flex flex-wrap justify-between gap-2">
                                    <b>{r.app_name} {r.version} · {r.ticket_id}</b>
                                    <span className={r.status === 'COMPLETED' ? 'text-green-700' : r.overdue ? 'text-red-700 font-semibold' : 'text-amber-700'}>
                                        {r.status === 'COMPLETED' ? 'Completed' : r.overdue ? 'OVERDUE' : `Due ${fmtDateTime(r.due_at)}`}
                                    </span>
                                </div>
                                <p className="text-gray-700 mt-1">Justification ({r.opened_by_username}): {r.justification}</p>
                                {r.findings && <p className="text-gray-700 mt-1">Findings: {r.findings}</p>}
                                {r.status === 'PENDING' && can(user, 'incident.review') && r.opened_by !== user.id && (
                                    <div className="flex gap-2 mt-2">
                                        <input className={inputClass} placeholder="Root cause and follow-up (min 20 chars)" value={findings[r.id] || ''}
                                            onChange={e => setFindings(f => ({ ...f, [r.id]: e.target.value }))} />
                                        <Button disabled={(findings[r.id] || '').trim().length < 20} onClick={() => complete(r.id)}>Complete</Button>
                                    </div>
                                )}
                            </li>
                        ))}
                    </ul>
                )}
            </Card>
        </div>
    );
};

export default GovernanceView;
