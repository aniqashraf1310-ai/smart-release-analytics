import { BarChart2, CheckCircle, Clock, Eye, Lock, TrendingUp, XCircle } from 'lucide-react';

export const PRIMARY_COLOR = '#7C3AED';
export const NEUTRAL_COLOR = '#64748B';

// QA tracker status (existing workflow field)
export const QA_STATUS_CONFIG = {
    'Open': { class: 'bg-blue-100 text-blue-700', icon: Clock },
    'Under review': { class: 'bg-yellow-100 text-yellow-700', icon: Eye },
    'Ready for UAT': { class: 'bg-indigo-100 text-indigo-700', icon: BarChart2 },
    'Other testing unit - Pass': { class: 'bg-green-100 text-green-700', icon: CheckCircle },
    'Pass': { class: 'bg-green-100 text-green-700', icon: CheckCircle },
    'Failed': { class: 'bg-red-100 text-red-700', icon: XCircle },
    'Confirmed to next release': { class: 'bg-purple-100 text-purple-700', icon: TrendingUp },
    'Descoped': { class: 'bg-gray-200 text-gray-700', icon: Lock },
    'Closed': { class: 'bg-gray-400 text-gray-800', icon: CheckCircle },
};
export const QA_STATUS_OPTIONS = Object.keys(QA_STATUS_CONFIG);

export const FIX_TYPE_OPTIONS = ['Binary Changes', 'SSL Pinning', 'Spotlight Configuration', 'Data Model Update', 'Other'];
export const CHANGE_TYPES = ['feature', 'hotfix', 'config', 'refactor'];
export const TARGET_ENVS = ['production', 'staging', 'development'];

// Governance status (state machine in the database)
export const GOV_STATUS_CONFIG = {
    DRAFT: { label: 'Draft', class: 'bg-gray-100 text-gray-700 border-gray-300' },
    IN_REVIEW: { label: 'In review', class: 'bg-amber-50 text-amber-800 border-amber-300' },
    APPROVED: { label: 'Approved', class: 'bg-green-50 text-green-800 border-green-300' },
    REJECTED: { label: 'Rejected', class: 'bg-red-50 text-red-700 border-red-300' },
    DEPLOYING: { label: 'Deploying', class: 'bg-indigo-50 text-indigo-700 border-indigo-300' },
    DEPLOYED: { label: 'Deployed', class: 'bg-emerald-100 text-emerald-800 border-emerald-400' },
    FAILED: { label: 'Failed', class: 'bg-red-100 text-red-800 border-red-400' },
    ROLLED_BACK: { label: 'Rolled back', class: 'bg-orange-100 text-orange-800 border-orange-400' },
    CANCELLED: { label: 'Cancelled', class: 'bg-gray-200 text-gray-500 border-gray-300' },
};

export const TIER_CONFIG = {
    LOW: { label: 'Low risk', class: 'bg-green-100 text-green-800', bar: '#2f855a', border: '#2f855a' },
    MEDIUM: { label: 'Medium risk', class: 'bg-yellow-100 text-yellow-800', bar: '#b7791f', border: '#d69e2e' },
    HIGH: { label: 'High risk', class: 'bg-orange-100 text-orange-800', bar: '#c05621', border: '#dd6b20' },
    CRITICAL: { label: 'Critical risk', class: 'bg-red-100 text-red-800', bar: '#9b2c2c', border: '#c53030' },
};

export const ROLE_LABELS = {
    DEVELOPER: 'Developer',
    QA_LEAD: 'QA Lead',
    RELEASE_MANAGER: 'Release Manager',
};

export const pct = (p, digits = 1) => (p === null || p === undefined ? '—' : `${(Number(p) * 100).toFixed(digits)}%`);
export const fmtDateTime = (s) => (s ? new Date(s).toLocaleString() : '—');
