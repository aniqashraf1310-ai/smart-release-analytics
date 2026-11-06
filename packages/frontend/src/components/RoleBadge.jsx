import React from 'react';
import { ROLE_INFO } from '../lib/permissions';

const RoleBadge = ({ role, className = '' }) => {
    const info = ROLE_INFO[role];
    if (!info) return <span className={`inline-flex text-xs px-2 py-0.5 rounded-full border bg-gray-100 text-gray-500 ${className}`}>No role</span>;
    const Icon = info.icon;
    return (
        <span className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full border ${info.badge} ${className}`}>
            <Icon className="w-3 h-3" /> {info.label}
        </span>
    );
};

export default RoleBadge;
