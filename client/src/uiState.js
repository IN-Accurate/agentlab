// Persisted UI location so refresh restores the current page, chat, and
// dashboard tab/project. Drill selections (run/result/trace/review) are
// intentionally transient and reset.

export const UI_KEY = 'agentlab.ui.v1';

const VIEWS = ['chat', 'dashboard', 'agents'];
const TABS = ['overview', 'runs', 'traces', 'improvements'];

function defaults() {
  return { view: 'chat', activeId: null, dash: { tab: 'overview', projectId: null } };
}

export function sanitizeUi(value) {
  const v = value && typeof value === 'object' ? value : {};
  const dash = v.dash && typeof v.dash === 'object' ? v.dash : {};
  return {
    view: VIEWS.includes(v.view) ? v.view : 'chat',
    activeId: typeof v.activeId === 'string' ? v.activeId : null,
    dash: {
      tab: TABS.includes(dash.tab) ? dash.tab : 'overview',
      projectId: typeof dash.projectId === 'string' ? dash.projectId : null,
    },
  };
}

export function loadUi() {
  try {
    const raw = localStorage.getItem(UI_KEY);
    if (!raw) return defaults();
    return sanitizeUi(JSON.parse(raw));
  } catch {
    return defaults();
  }
}

export function saveUi(ui) {
  try {
    localStorage.setItem(UI_KEY, JSON.stringify(sanitizeUi(ui)));
  } catch {
    // Private browsing: location lasts for this tab only.
  }
}
