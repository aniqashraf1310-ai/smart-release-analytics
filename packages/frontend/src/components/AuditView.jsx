import React, { useEffect, useState } from 'react';
import { ShieldCheck, ShieldX } from 'lucide-react';
import { api } from '../lib/api';
import { fmtDateTime } from '../lib/constants';
import { Button, Card, ErrorBox } from './ui';

const AuditView = () => {
    const [entries, setEntries] = useState([]);
    const [verify, setVerify] = useState(null);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);

    useEffect(() => { api('/api/audit?limit=300').then(r => setEntries(r.entries)).catch(e => setError(e.message)); }, []);

    const check = async () => {
        setBusy(true);
        try { setVerify(await api('/api/audit/verify')); } catch (e) { setError(e.message); } finally { setBusy(false); }
    };

    return (
        <Card title="Tamper-evident audit log"
            subtitle="Append-only. Each entry stores the SHA-256 hash of the previous one, so editing or deleting any row breaks the chain."
            actions={<Button onClick={check} disabled={busy}><ShieldCheck className="w-4 h-4" /> Verify hash chain</Button>}>
            <ErrorBox error={error} />
            {verify && (
                <div className={`mb-4 p-3 rounded-lg text-sm flex items-center gap-2 ${verify.is_valid ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'}`}>
                    {verify.is_valid ? <ShieldCheck className="w-5 h-5" /> : <ShieldX className="w-5 h-5" />}
                    {verify.is_valid
                        ? `Chain intact: all ${verify.rows_checked} entries verified.`
                        : `Chain BROKEN at entry #${verify.first_invalid_seq}: ${verify.reason}.`}
                </div>
            )}
            <div className="overflow-x-auto">
                <table className="w-full text-xs">
                    <thead className="text-left text-neutral">
                        <tr><th className="py-1 pr-2">#</th><th className="pr-2">When</th><th className="pr-2">Actor</th><th className="pr-2">Action</th><th className="pr-2">Entity</th><th className="pr-2">Details</th><th>Hash</th></tr>
                    </thead>
                    <tbody>
                        {entries.map(e => (
                            <tr key={e.seq} className="border-t align-top">
                                <td className="py-1 pr-2 tabular-nums">{e.seq}</td>
                                <td className="pr-2 whitespace-nowrap">{fmtDateTime(e.occurred_at)}</td>
                                <td className="pr-2">{e.actor || 'system'}</td>
                                <td className="pr-2 font-medium">{e.action}</td>
                                <td className="pr-2">{e.entity} {e.entity_id}</td>
                                <td className="pr-2 max-w-xs truncate" title={JSON.stringify(e.details)}>{JSON.stringify(e.details)}</td>
                                <td className="font-mono" title={`prev ${e.prev_hash}\nthis ${e.row_hash}`}>{e.row_hash.slice(0, 12)}…</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Card>
    );
};

export default AuditView;
