// Public links shown on the site (env-configurable for forks and staging).
export const REPO_URL = (process.env.NEXT_PUBLIC_REPO_URL ?? 'https://github.com/moeedkundi/doc-agent').replace(/\/$/, '')
export const repoFile = (path: string) => `${REPO_URL}/blob/master/${path}`
