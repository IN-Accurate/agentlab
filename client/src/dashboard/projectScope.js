// The dashboard shows only the user's own agents: those saved in Agent Manager on
// this browser, plus the members of any team they connected. The API has no user
// accounts yet, so this is a view filter, not an access control.

export function ownProjectIds(agents) {
  const ids = [];
  for (const agent of agents ?? []) {
    for (const id of [agent?.projectId, ...(agent?.memberProjectIds ?? [])]) {
      if (typeof id === 'string' && id && !ids.includes(id)) ids.push(id);
    }
  }
  return ids;
}

/** Fetches each own project by id; a deleted project (404) is skipped, other errors throw. */
export async function fetchOwnProjects(api, ids) {
  const found = await Promise.all(
    ids.map((id) =>
      api(`/v1/projects/${id}`).catch((error) => {
        if (error?.status === 404) return null;
        throw error;
      }),
    ),
  );
  return labelDuplicates(found.filter(Boolean));
}

/** Two agents with the same name get a short id suffix so the picker can tell them apart. */
export function labelDuplicates(projects) {
  const counts = {};
  for (const p of projects) counts[p.name] = (counts[p.name] ?? 0) + 1;
  return projects.map((p) => ({
    ...p,
    label: counts[p.name] > 1 ? `${p.name} · ${String(p.id).slice(0, 6)}` : p.name,
  }));
}

/** The selected project if it is still one of the user's, else their first one. */
export function selectedProject(projects, projectId) {
  return projects.find((p) => p.id === projectId) ?? projects[0] ?? null;
}

// Selections that belong to one project; cleared whenever the project changes.
export const PROJECT_RESET = {
  runId: null,
  resultId: null,
  traceId: null,
  review: null,
  spanId: null,
  runVersion: 'all',
  traceVersion: 'all',
  compareA: null,
  compareB: null,
};
