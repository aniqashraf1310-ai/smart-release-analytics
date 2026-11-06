import React, { useState } from 'react';
import { AlertTriangle, CheckCircle2, Circle, Rocket, Send, ShieldAlert, XCircle } from 'lucide-react';
import { api } from '../lib/api';
import { ROLE_LABELS, fmtDateTime } from '../lib/constants';
import { can } from '../lib/permissions';
import { Button, ErrorBox, TierBadge, inputClass } from './ui';

// Shows the risk-adaptive signature requirements and the actions the
// current user is allowed to take. The database enforces every rule; the UI
// only hides buttons that would certainly be refused.
const ApprovalPanel = ({ release, user, onChanged }) => {
    const [comment, setComment] = useState('');
    const [ack, setAck] = useState(false);
    const [justification, setJustification] = useState('');
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);

    const run = async (path, body) => {
        setBusy(true);
        setError(null);
        try {
            const res = await api(`/api/releases/${release.id}/${path}`, { method: 'POST', body: body || {} });
            setComment('');
            setAck(false);
            onChanged(res.release || res);
        } catch (e) {
            setError(e.message);
        } finally {
            setBusy(false);
        }
    };

    const status = release.gov_status;
    const progress = release.approval_progress || [];
    const live = (release.approvals || []).filter(a => a.is_live);
    const iSigned = live.some(a => a.username === user.username);
    const isSubmitter = release.submitted_by === user.id;
    const SIGN_PERMISSION = { QA_LEAD: 'approval.qa', RELEASE_MANAGER: 'approval.rm' };
    const signable = progress.filter(p => SIGN_PERMISSION[p.role_code] && can(user, SIGN_PERMISSION[p.role_code]) && p.approved < p.required);
    const needsAck = signable.some(p => p.requires_risk_ack);
    const drivers = release.risk_explanation?.top_risk_drivers || [];

    return (
        <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-gray-600">Required sign-offs for</span>
                <TierBadge tier={release.risk_tier} probability={release.risk_probability} />
                <span className="text-neutral">· revision {release.revision}</span>
                {release.is_emergency && (
                    <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-red-600 text-white">
                        <ShieldAlert className="w-3 h-3" /> Break-glass
                    </span>
                )}
            </div>

            {progress.length === 0 ? (
                <p className="text-sm text-neutral">Score the release (Risk tab) to see which signatures its risk tier requires.</p>
            ) : (
                <ul className="divide-y border rounded-lg">
                    {progress.map(p => {
                        const done = p.approved >= p.required;
                        return (
                            <li key={p.role_code} className="flex flex-wrap items-center gap-3 p-3 text-sm">
                                {done ? <CheckCircle2 className="w-5 h-5 text-green-600" /> : <Circle className="w-5 h-5 text-gray-300" />}
                                <span className="font-medium text-gray-800 w-40">{ROLE_LABELS[p.role_code]}</span>
                                <span className="tabular-nums text-gray-600">{p.approved}/{p.required}</span>
                                {p.requires_risk_ack && <span className="text-xs text-amber-700 bg-amber-50 px-2 py-0.5 rounded">risk acknowledgement</span>}
                                <span className="text-xs text-neutral">{p.signers.join(', ')}</span>
                            </li>
                        );
                    })}
                </ul>
            )}

            <ErrorBox error={error} />

            {/* Developer: submit */}
            {status === 'DRAFT' && can(user, 'release.submit') && (
                <div className="flex flex-wrap gap-2 items-center">
                    <input className={`${inputClass} flex-1 min-w-[12rem]`} placeholder="Submission note (optional)" value={comment} onChange={e => setComment(e.target.value)} />
                    <Button disabled={busy || !release.risk_tier} onClick={() => run('submit', { comment: comment || null })}>
                        <Send className="w-4 h-4" /> Submit for review
                    </Button>
                    {!release.risk_tier && <span className="text-xs text-neutral w-full">Record a risk score first.</span>}
                </div>
            )}

            {/* Reviewers: approve / reject */}
            {status === 'IN_REVIEW' && signable.length > 0 && !iSigned && !isSubmitter && (
                <div className="border rounded-lg p-4 space-y-3 bg-gray-50">
                    {needsAck && drivers.length > 0 && (
                        <div className="text-sm bg-amber-50 border border-amber-200 rounded p-3">
                            <div className="font-semibold text-amber-800 flex items-center gap-1 mb-1"><AlertTriangle className="w-4 h-4" /> Risk drivers to acknowledge</div>
                            <ul className="list-disc ml-5 text-amber-900">
                                {drivers.map(d => <li key={d.feature}>{d.label}: {String(d.value)} (+{d.contribution_pp.toFixed(1)} pts)</li>)}
                            </ul>
                        </div>
                    )}
                    <textarea className={inputClass} rows={2} placeholder="Comment (required when rejecting)" value={comment} onChange={e => setComment(e.target.value)} />
                    {needsAck && (
                        <label className="flex items-center gap-2 text-sm text-gray-700">
                            <input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />
                            I have reviewed the risk drivers above and accept them for this release
                        </label>
                    )}
                    <div className="flex flex-wrap gap-2">
                        {signable.map(p => (
                            <Button key={p.role_code} disabled={busy || (p.requires_risk_ack && !ack)}
                                onClick={() => run('approvals', { role: p.role_code, decision: 'APPROVE', comment: comment || null, acknowledged_risk: ack })}>
                                <CheckCircle2 className="w-4 h-4" /> Approve as {ROLE_LABELS[p.role_code]}
                            </Button>
                        ))}
                        <Button variant="danger" disabled={busy || !comment.trim()}
                            onClick={() => run('approvals', { role: signable[0].role_code, decision: 'REJECT', comment })}>
                            <XCircle className="w-4 h-4" /> Reject
                        </Button>
                    </div>
                </div>
            )}
            {status === 'IN_REVIEW' && !iSigned && !isSubmitter && signable.length === 0 && !can(user, 'release.submit') && (
                <p className="text-sm text-neutral">No sign-off is needed from your role right now.</p>
            )}
            {status === 'IN_REVIEW' && can(user, 'release.submit') && !isSubmitter && (
                <p className="text-sm text-neutral">Developers cannot approve releases. Waiting for QA Lead and Release Manager sign-off.</p>
            )}
            {status === 'IN_REVIEW' && (iSigned || isSubmitter) && (
                <p className="text-sm text-neutral">
                    {isSubmitter ? 'You submitted this release, so you cannot approve it (separation of duties).' : 'You have already signed this revision.'}
                </p>
            )}

            {/* Release manager: deploy + outcome */}
            {can(user, 'release.deploy') && status === 'APPROVED' && (
                <Button disabled={busy} onClick={() => run('deploy')}><Rocket className="w-4 h-4" /> Start deployment</Button>
            )}
            {can(user, 'release.deploy') && (status === 'DEPLOYING' || status === 'DEPLOYED') && (
                <div className="flex flex-wrap gap-2">
                    {status === 'DEPLOYING' && <Button disabled={busy} onClick={() => run('outcome', { outcome: 'DEPLOYED' })}>Mark deployed</Button>}
                    {status === 'DEPLOYING' && <Button variant="danger" disabled={busy} onClick={() => run('outcome', { outcome: 'FAILED', notes: comment || null })}>Mark failed</Button>}
                    <Button variant="secondary" disabled={busy} onClick={() => run('outcome', { outcome: 'ROLLED_BACK', notes: comment || null })}>Record rollback</Button>
                </div>
            )}

            {/* Head of engineering: break-glass */}
            {can(user, 'release.break_glass') && status === 'IN_REVIEW' && !isSubmitter && !iSigned && (
                <details className="border border-red-200 rounded-lg p-3">
                    <summary className="text-sm font-semibold text-red-700 cursor-pointer">Emergency break-glass approval</summary>
                    <p className="text-xs text-gray-600 my-2">
                        Approves with missing signatures. The release is flagged, the justification is audited, and a different Release Manager must complete a post-incident review.
                    </p>
                    <textarea className={inputClass} rows={2} placeholder="Justification (at least 20 characters)" value={justification} onChange={e => setJustification(e.target.value)} />
                    <Button variant="danger" className="mt-2" disabled={busy || justification.trim().length < 20}
                        onClick={() => run('emergency-approval', { justification })}>
                        <ShieldAlert className="w-4 h-4" /> Break glass
                    </Button>
                </details>
            )}

            {(release.approvals || []).length > 0 && (
                <div>
                    <h4 className="text-sm font-semibold text-gray-700 mb-2">Signature history</h4>
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead className="text-left text-neutral">
                                <tr><th className="py-1 pr-2">Rev</th><th className="pr-2">Role</th><th className="pr-2">User</th><th className="pr-2">Decision</th><th className="pr-2">Signed</th><th className="pr-2">State</th><th>Content hash</th></tr>
                            </thead>
                            <tbody>
                                {release.approvals.map(a => (
                                    <tr key={a.id} className={`border-t ${a.is_live ? '' : 'text-gray-400'}`}>
                                        <td className="py-1 pr-2">{a.release_revision}</td>
                                        <td className="pr-2">{ROLE_LABELS[a.role_code]}</td>
                                        <td className="pr-2">{a.username}</td>
                                        <td className="pr-2">{a.decision}{a.comment ? ` – ${a.comment}` : ''}</td>
                                        <td className="pr-2 whitespace-nowrap">{fmtDateTime(a.signed_at)}</td>
                                        <td className="pr-2">{a.invalidated_at ? `invalidated (${a.invalidated_reason})` : a.is_live ? 'valid' : 'expired'}</td>
                                        <td className="font-mono" title={a.payload_hash}>{a.payload_hash.slice(0, 10)}…</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
};

export default ApprovalPanel;
