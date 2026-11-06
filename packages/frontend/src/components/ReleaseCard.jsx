import React from 'react';
import { ShieldAlert, Tag, User } from 'lucide-react';
import { QA_STATUS_CONFIG, TIER_CONFIG } from '../lib/constants';
import { GovStatusBadge, TierBadge } from './ui';

const ReleaseCard = ({ release, onOpen }) => {
    const qa = QA_STATUS_CONFIG[release.qa_status] || QA_STATUS_CONFIG.Open;
    const QaIcon = qa.icon;
    const border = release.risk_tier ? TIER_CONFIG[release.risk_tier].border : '#d1d5db';
    return (
        <button
            onClick={() => onOpen(release)}
            className="w-full text-left bg-white p-5 rounded-xl shadow-md border-l-8 transition hover:shadow-lg flex flex-col gap-2"
            style={{ borderColor: border }}
        >
            <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-lg font-bold text-gray-800">
                    {release.app_name} <span className="font-normal text-neutral">({release.version})</span>
                </h3>
                <GovStatusBadge status={release.gov_status} />
                <TierBadge tier={release.risk_tier} probability={release.risk_probability} />
                {release.is_emergency && <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-red-600 text-white"><ShieldAlert className="w-3 h-3" /> Break-glass</span>}
                {release.pending_reviews > 0 && <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200">Incident review due</span>}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-gray-600">
                <span className={`inline-flex items-center text-xs font-semibold px-2 py-0.5 rounded-full ${qa.class}`}><QaIcon className="w-3 h-3 mr-1" />{release.qa_status}</span>
                <span>Date: <b className="text-gray-700">{release.target_date}</b></span>
                <span className="flex items-center text-primary"><User className="w-3 h-3 mr-1" />QA: {release.responsible_qa}</span>
                <span className="flex items-center"><Tag className="w-3 h-3 mr-1 text-neutral" /><span className="font-mono text-xs bg-gray-100 px-1.5 py-0.5 rounded">{release.ticket_id}</span></span>
                <span className="text-xs text-neutral self-center">{release.fix_type} · {release.target_env}</span>
            </div>
            <p className="text-sm text-gray-700 truncate">{release.description}</p>
        </button>
    );
};

export default ReleaseCard;
