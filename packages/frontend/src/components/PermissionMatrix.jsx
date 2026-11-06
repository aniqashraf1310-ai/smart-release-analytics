import React, { useEffect, useState } from 'react';
import { Check, Minus } from 'lucide-react';
import { api } from '../lib/api';
import { ROLE_INFO, ROLES } from '../lib/permissions';

// Who can do what, straight from the server's permission matrix.
const PermissionMatrix = ({ highlightRole }) => {
    const [rows, setRows] = useState([]);
    useEffect(() => { api('/api/auth/permissions').then(d => setRows(d.permissions)).catch(() => {}); }, []);
    if (rows.length === 0) return null;
    let lastGroup = null;
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-sm">
                <thead>
                    <tr className="text-left">
                        <th className="py-2 pr-3 text-neutral font-medium">Action</th>
                        {ROLES.map(r => (
                            <th key={r} className={`py-2 px-2 text-center font-semibold ${r === highlightRole ? 'bg-primary-50 text-primary rounded-t' : 'text-gray-700'}`}>
                                {ROLE_INFO[r].label}{r === highlightRole ? ' (you)' : ''}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map(p => {
                        const header = p.group !== lastGroup;
                        lastGroup = p.group;
                        return (
                            <React.Fragment key={p.code}>
                                {header && (
                                    <tr><td colSpan={4} className="pt-3 pb-1 text-xs font-bold uppercase tracking-wide text-neutral">{p.group}</td></tr>
                                )}
                                <tr className="border-t">
                                    <td className="py-1.5 pr-3 text-gray-700">{p.description}</td>
                                    {ROLES.map(r => (
                                        <td key={r} className={`py-1.5 px-2 text-center ${r === highlightRole ? 'bg-primary-50' : ''}`}>
                                            {p.roles[r]
                                                ? <Check className="w-4 h-4 text-green-600 inline" aria-label="allowed" />
                                                : <Minus className="w-4 h-4 text-gray-300 inline" aria-label="not allowed" />}
                                        </td>
                                    ))}
                                </tr>
                            </React.Fragment>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
};

export default PermissionMatrix;
