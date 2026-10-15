// JSON requests to this origin. Errors carry the HTTP status for the view to explain.
export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function getJSON(path, { signal } = {}) {
  let res;
  try {
    res = await fetch(path, { headers: { Accept: 'application/json' }, credentials: 'same-origin', signal });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, 'The dashboard server could not be reached.');
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) throw new ApiError(res.status, (body && body.error) || `Request failed (${res.status}).`);
  return body;
}

export const stagePath = (id) => `/api/stage/${encodeURIComponent(id)}`;
export const urlPath = (url) => `/api/url?u=${encodeURIComponent(url)}`;
export const explorePath = (segments) => `/api/explore?path=${encodeURIComponent(segments.join('/'))}`;
