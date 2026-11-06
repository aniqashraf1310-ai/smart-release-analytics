import React from 'react';
import { Bell, CheckCircle2, X, XCircle } from 'lucide-react';

const STYLE = {
    success: { icon: CheckCircle2, ring: 'ring-emerald-200', iconClass: 'text-emerald-600 bg-emerald-50' },
    error: { icon: XCircle, ring: 'ring-red-200', iconClass: 'text-red-600 bg-red-50' },
    info: { icon: Bell, ring: 'ring-primary-200', iconClass: 'text-primary bg-primary-50' },
};

// Toast shown top-right. `onClick` makes the whole toast clickable (e.g. open the release).
const Notification = ({ message, title, type = 'success', onClose, onClick }) => {
    if (!message) return null;
    const s = STYLE[type] || STYLE.info;
    const Icon = s.icon;
    return (
        <div role="status" aria-live="polite"
            className={`fixed top-5 right-5 z-[60] w-[min(24rem,calc(100vw-2.5rem))] bg-white rounded-2xl shadow-2xl ring-1 ${s.ring} p-4 flex gap-3 items-start ${onClick ? 'cursor-pointer hover:bg-slate-50' : ''}`}
            onClick={onClick}>
            <div className={`rounded-xl p-2 ${s.iconClass}`}><Icon className="w-5 h-5" /></div>
            <div className="flex-1 min-w-0">
                {title && <div className="font-semibold text-slate-900">{title}</div>}
                <div className="text-sm text-slate-600">{message}</div>
            </div>
            <button aria-label="Dismiss" onClick={(e) => { e.stopPropagation(); onClose(); }}
                className="p-1 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100">
                <X className="w-4 h-4" />
            </button>
        </div>
    );
};

export default Notification;
