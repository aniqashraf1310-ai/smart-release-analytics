import React, { useEffect, useState } from 'react';
import { Check, UserCheck, UserX, X } from 'lucide-react';
import { api } from '../lib/api';
import { fmtDateTime } from '../lib/constants';
import { ROLE_INFO, ROLES } from '../lib/permissions';
import RoleBadge from './RoleBadge';
import { Button, Card, ErrorBox, inputClass } from './ui';

const STATUS_STYLE = {
    PENDING: 'bg-amber-50 text-amber-800 border-amber-300',
    ACTIVE: 'bg-green-50 text-green-800 border-green-300',
    REJECTED: 'bg-gray-100 text-gray-500 border-gray-300',
    DISABLED: 'bg-red-50 text-red-700 border-red-300',
};

const PendingRequest = ({ u, onDone, setError }) => {
    const [role, setRole] = useState(u.requested_role || 'DEVELOPER');
    const [note, setNote] = useState('');
    const [busy, setBusy] = useState(false);
    const act = async (path, body) => {
        setBusy(true);
        try { await api(`/api/users/${u.id}/${path}`, { method: 'POST', body }); onDone(); }
        catch (e) { setError(e.message); }
        finally { setBusy(false); }
    };
    return (
        <li className="border rounded-lg p-4 flex flex-col gap-3 md:flex-row md:items-center">
            <div className="flex-1 min-w-0">
                <div className="font-semibold text-gray-800">{u.full_name} <span className="font-normal text-neutral">@{u.username}</span></div>
                <div className="text-xs text-neutral">{u.email || 'no email'} · registered {fmtDateTime(u.created_at)}</div>
                <div className="text-sm mt-1 flex items-center gap-1">Requested: <RoleBadge role={u.requested_role} /></div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <select className={`${inputClass} w-auto`} value={role} onChange={e => setRole(e.target.value)} aria-label="Role to grant">
                    {ROLES.map(r => <option key={r} value={r}>Grant {ROLE_INFO[r].label}</option>)}
                </select>
                <input className={`${inputClass} w-44`} placeholder="Note (optional)" value={note} onChange={e => setNote(e.target.value)} />
                <Button disabled={busy} onClick={() => act('approve', { role, note: note || null })}><Check className="w-4 h-4" /> Approve</Button>
                <Button variant="secondary" disabled={busy} onClick={() => act('reject', { note: note || null })}><X className="w-4 h-4" /> Reject</Button>
            </div>
            {role !== u.requested_role && (
                <p className="text-xs text-amber-700 md:hidden">You are granting a different role from the one requested.</p>
            )}
        </li>
    );
};

const UsersView = ({ user }) => {
    const [users, setUsers] = useState([]);
    const [error, setError] = useState(null);
    const [filter, setFilter] = useState('');

    const load = () => api('/api/users').then(d => { setUsers(d.users); setError(null); }).catch(e => setError(e.message));
    useEffect(() => { load(); }, []);

    const put = async (u, path, body) => {
        try { await api(`/api/users/${u.id}/${path}`, { method: 'PUT', body }); load(); }
        catch (e) { setError(e.message); }
    };

    const pending = users.filter(u => u.status === 'PENDING');
    const others = users.filter(u => u.status !== 'PENDING' && (!filter || u.role === filter || u.status === filter));
    const counts = Object.fromEntries(ROLES.map(r => [r, users.filter(u => u.role === r && u.status === 'ACTIVE').length]));

    return (
        <div className="space-y-6">
            <ErrorBox error={error} />
            <div className="grid grid-cols-3 gap-3">
                {ROLES.map(r => (
                    <div key={r} className="bg-white rounded-xl shadow p-4 border-l-4" style={{ borderColor: ROLE_INFO[r].accent }}>
                        <div className="text-2xl font-bold tabular-nums text-gray-800">{counts[r]}</div>
                        <div className="text-xs text-neutral">active {ROLE_INFO[r].label}{counts[r] === 1 ? '' : 's'}</div>
                    </div>
                ))}
            </div>

            <Card title={`Pending registrations (${pending.length})`} subtitle="New accounts have no access until you approve them and choose their role.">
                {pending.length === 0
                    ? <p className="text-sm text-neutral">No registrations waiting.</p>
                    : <ul className="space-y-3">{pending.map(u => <PendingRequest key={u.id} u={u} onDone={load} setError={setError} />)}</ul>}
            </Card>

            <Card title="All accounts"
                subtitle="Role changes and disabling take effect immediately. You cannot change your own account."
                actions={
                    <select className={`${inputClass} w-auto`} value={filter} onChange={e => setFilter(e.target.value)} aria-label="Filter accounts">
                        <option value="">All</option>
                        {ROLES.map(r => <option key={r} value={r}>{ROLE_INFO[r].label}s</option>)}
                        <option value="DISABLED">Disabled</option>
                        <option value="REJECTED">Rejected</option>
                    </select>
                }>
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="text-left text-neutral">
                            <tr><th className="py-2 pr-3">User</th><th className="pr-3">Role</th><th className="pr-3">Status</th><th className="pr-3">Reviewed</th><th className="text-right">Actions</th></tr>
                        </thead>
                        <tbody>
                            {others.map(u => {
                                const self = u.id === user.id;
                                return (
                                    <tr key={u.id} className="border-t align-middle">
                                        <td className="py-2 pr-3">
                                            <div className="font-medium text-gray-800">{u.full_name}{self && <span className="text-xs text-neutral"> (you)</span>}</div>
                                            <div className="text-xs text-neutral">@{u.username}{u.email ? ` · ${u.email}` : ''}</div>
                                        </td>
                                        <td className="pr-3">
                                            {u.status === 'ACTIVE' && !self ? (
                                                <select className="text-sm border rounded-md p-1" value={u.role || ''} aria-label={`Role of ${u.username}`}
                                                    onChange={e => window.confirm(`Change ${u.username} to ${ROLE_INFO[e.target.value].label}?`) && put(u, 'role', { role: e.target.value })}>
                                                    {ROLES.map(r => <option key={r} value={r}>{ROLE_INFO[r].label}</option>)}
                                                </select>
                                            ) : <RoleBadge role={u.role} />}
                                        </td>
                                        <td className="pr-3"><span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${STATUS_STYLE[u.status]}`}>{u.status.toLowerCase()}</span></td>
                                        <td className="pr-3 text-xs text-neutral">{u.reviewed_by_username ? `${u.reviewed_by_username}, ${fmtDateTime(u.reviewed_at)}` : 'seeded'}{u.review_note ? ` – ${u.review_note}` : ''}</td>
                                        <td className="text-right">
                                            {!self && u.status === 'ACTIVE' && (
                                                <button className="text-xs text-red-700 hover:underline inline-flex items-center gap-1"
                                                    onClick={() => window.confirm(`Disable ${u.username}? They lose all access immediately.`) && put(u, 'status', { status: 'DISABLED' })}>
                                                    <UserX className="w-3.5 h-3.5" /> Disable
                                                </button>
                                            )}
                                            {!self && u.status === 'DISABLED' && (
                                                <button className="text-xs text-green-700 hover:underline inline-flex items-center gap-1" onClick={() => put(u, 'status', { status: 'ACTIVE' })}>
                                                    <UserCheck className="w-3.5 h-3.5" /> Re-enable
                                                </button>
                                            )}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            </Card>
        </div>
    );
};

export default UsersView;
