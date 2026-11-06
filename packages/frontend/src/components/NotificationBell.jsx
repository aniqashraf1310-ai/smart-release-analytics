import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
    AlertTriangle, Bell, BellRing, CheckCheck, CheckCircle2, Clock, Pencil, Rocket, Send,
    ShieldAlert, UserCheck, UserPlus, XCircle,
} from 'lucide-react';
import { api, session } from '../lib/api';

// Notifications are pushed instantly over Server-Sent Events. Polling is only a
// safety net for when the live connection is down (e.g. API restarting).
const POLL_MS = 30000;

const KIND = {
    review_requested: { icon: Send, cls: 'text-primary bg-primary-50' },
    signed: { icon: CheckCircle2, cls: 'text-emerald-600 bg-emerald-50' },
    approved: { icon: CheckCheck, cls: 'text-emerald-700 bg-emerald-100' },
    rejected: { icon: XCircle, cls: 'text-red-600 bg-red-50' },
    break_glass: { icon: ShieldAlert, cls: 'text-red-700 bg-red-100' },
    deploying: { icon: Rocket, cls: 'text-indigo-600 bg-indigo-50' },
    deployed: { icon: Rocket, cls: 'text-emerald-700 bg-emerald-50' },
    failed: { icon: AlertTriangle, cls: 'text-orange-700 bg-orange-50' },
    revised: { icon: Pencil, cls: 'text-amber-700 bg-amber-50' },
    edited: { icon: Pencil, cls: 'text-cyan-700 bg-cyan-50' },
    expired: { icon: Clock, cls: 'text-amber-700 bg-amber-50' },
    cancelled: { icon: XCircle, cls: 'text-slate-500 bg-slate-100' },
    review_completed: { icon: CheckCheck, cls: 'text-primary bg-primary-50' },
    account_request: { icon: UserPlus, cls: 'text-pink-600 bg-pink-50' },
    account_approved: { icon: UserCheck, cls: 'text-emerald-600 bg-emerald-50' },
    role_changed: { icon: UserCheck, cls: 'text-pink-600 bg-pink-50' },
};

const ago = (iso) => {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return `${Math.floor(s / 86400)} d ago`;
};

const desktopSupported = () => typeof window !== 'undefined' && 'Notification' in window;

// Bell with unread badge and dropdown. A live Server-Sent-Events connection
// tells it the instant a new notification is committed in the database; it
// then fetches it, shows a toast and, if the user allowed it and the tab is in
// the background, a desktop notification. It also re-checks on window focus,
// when `refreshKey` changes, and every 30 s as a fallback.
const NotificationBell = ({ onOpenRelease, onOpenPage, onToast, refreshKey }) => {
    const [open, setOpen] = useState(false);
    const [items, setItems] = useState([]);
    const [unread, setUnread] = useState(0);
    const [live, setLive] = useState(false);
    const [permission, setPermission] = useState(desktopSupported() ? window.Notification.permission : 'unsupported');
    const latestSeen = useRef(null);
    const panelRef = useRef(null);

    const loadList = useCallback(async () => {
        const d = await api('/api/notifications?limit=30');
        setItems(d.notifications);
        setUnread(d.unread);
        return d.notifications;
    }, []);

    const poll = useCallback(async () => {
        try {
            const c = await api('/api/notifications/unread-count');
            setUnread(c.unread);
            if (latestSeen.current === null) {             // first load: no alerts for old items
                latestSeen.current = c.latest_id;
                return;
            }
            if (c.latest_id > latestSeen.current) {
                const list = await loadList();
                const fresh = list.filter(n => n.id > latestSeen.current && !n.read_at).reverse();
                latestSeen.current = c.latest_id;
                const newest = fresh[fresh.length - 1];
                if (newest) {
                    onToast?.({ title: fresh.length > 1 ? `${newest.title} (+${fresh.length - 1} more)` : newest.title,
                                message: newest.message, releaseId: newest.release_id });
                }
                if (document.hidden && desktopSupported() && window.Notification.permission === 'granted') {
                    fresh.slice(-3).forEach(n => {
                        const d = new window.Notification(n.title, { body: n.message, tag: `sras-${n.id}` });
                        d.onclick = () => { window.focus(); if (n.release_id) onOpenRelease(n.release_id); };
                    });
                }
            }
        } catch {
            /* offline or signed out - try again next tick */
        }
    }, [loadList, onOpenRelease, onToast]);

    useEffect(() => {
        poll();
        const t = setInterval(poll, POLL_MS);
        const onFocus = () => poll();
        window.addEventListener('focus', onFocus);
        return () => { clearInterval(t); window.removeEventListener('focus', onFocus); };
    }, [poll]);

    useEffect(() => { if (refreshKey) poll(); }, [refreshKey, poll]);

    // Live push channel. EventSource reconnects by itself after a drop.
    useEffect(() => {
        if (typeof window.EventSource === 'undefined' || !session.token) return undefined;
        const es = new window.EventSource(`/api/notifications/stream?token=${encodeURIComponent(session.token)}`);
        es.addEventListener('ready', () => { setLive(true); poll(); });
        es.addEventListener('notification', () => poll());
        es.addEventListener('revoked', () => { es.close(); setLive(false); });
        es.onerror = () => setLive(false);
        return () => es.close();
    }, [poll]);
    useEffect(() => { if (open) loadList().catch(() => {}); }, [open, loadList]);

    useEffect(() => {
        if (!open) return undefined;
        const onDown = (e) => { if (panelRef.current && !panelRef.current.contains(e.target)) setOpen(false); };
        const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
    }, [open]);

    const openItem = async (n) => {
        if (!n.read_at) {
            api(`/api/notifications/${n.id}/read`, { method: 'POST' }).catch(() => {});
            setItems(list => list.map(x => (x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x)));
            setUnread(u => Math.max(0, u - 1));
        }
        setOpen(false);
        if (n.release_id) onOpenRelease(n.release_id);
        else if (n.kind === 'account_request') onOpenPage?.('users');
    };

    const readAll = async () => {
        await api('/api/notifications/read-all', { method: 'POST' }).catch(() => {});
        setItems(list => list.map(x => ({ ...x, read_at: x.read_at || new Date().toISOString() })));
        setUnread(0);
    };

    const askDesktop = async () => {
        if (!desktopSupported()) return;
        setPermission(await window.Notification.requestPermission());
    };

    const BellIcon = unread > 0 ? BellRing : Bell;

    return (
        <div className="relative" ref={panelRef}>
            <button onClick={() => setOpen(o => !o)} aria-label={`Notifications (${unread} unread)`} aria-expanded={open}
                title={live ? 'Live: notifications arrive instantly' : 'Reconnecting: checking every 30 seconds'}
                className="relative p-2 rounded-xl bg-white/15 hover:bg-white/25 ring-1 ring-white/30 transition">
                <span className={`absolute -bottom-0.5 -left-0.5 w-2.5 h-2.5 rounded-full ring-2 ring-white ${live ? 'bg-emerald-400' : 'bg-slate-300'}`}
                    aria-hidden="true" />
                <BellIcon className={`w-5 h-5 ${unread > 0 ? 'animate-[wiggle_1s_ease-in-out_1]' : ''}`} />
                {unread > 0 && (
                    <span className="absolute -top-1.5 -right-1.5 min-w-[1.25rem] h-5 px-1 rounded-full bg-amber-300 text-slate-900 text-xs font-bold flex items-center justify-center ring-2 ring-white">
                        {unread > 99 ? '99+' : unread}
                    </span>
                )}
            </button>
            {open && (
                <div className="absolute right-0 mt-2 w-[min(24rem,calc(100vw-2rem))] bg-white text-slate-800 rounded-2xl shadow-2xl ring-1 ring-primary-100 z-50 overflow-hidden">
                    <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
                        <div className="font-semibold">
                            Notifications {unread > 0 && <span className="text-primary">· {unread} new</span>}
                            <span className={`ml-2 text-xs font-medium ${live ? 'text-emerald-600' : 'text-neutral'}`}>{live ? '● Live' : '○ Reconnecting'}</span>
                        </div>
                        {unread > 0 && <button onClick={readAll} className="text-xs font-semibold text-primary hover:underline">Mark all read</button>}
                    </div>
                    {permission === 'default' && (
                        <button onClick={askDesktop} className="w-full text-left px-4 py-2 text-xs bg-primary-50 text-primary-700 hover:bg-primary-100">
                            Turn on desktop alerts, so you're notified even when this tab is in the background
                        </button>
                    )}
                    <ul className="max-h-[26rem] overflow-y-auto divide-y divide-slate-100">
                        {items.length === 0 && <li className="px-4 py-8 text-center text-sm text-neutral">You're all caught up.</li>}
                        {items.map(n => {
                            const k = KIND[n.kind] || KIND.review_requested;
                            const Icon = k.icon;
                            return (
                                <li key={n.id}>
                                    <button onClick={() => openItem(n)}
                                        className={`w-full text-left px-4 py-3 flex gap-3 hover:bg-slate-50 ${n.read_at ? '' : 'bg-primary-50/40'}`}>
                                        <span className={`shrink-0 rounded-xl p-2 h-fit ${k.cls}`}><Icon className="w-4 h-4" /></span>
                                        <span className="flex-1 min-w-0">
                                            <span className="flex items-center gap-2">
                                                <span className="font-semibold text-sm text-slate-900">{n.title}</span>
                                                {!n.read_at && <span className="w-2 h-2 rounded-full bg-accent" aria-label="unread" />}
                                            </span>
                                            <span className="block text-sm text-slate-600 line-clamp-2">{n.message}</span>
                                            <span className="block text-xs text-neutral mt-0.5">{ago(n.created_at)}</span>
                                        </span>
                                    </button>
                                </li>
                            );
                        })}
                    </ul>
                </div>
            )}
        </div>
    );
};

export default NotificationBell;
