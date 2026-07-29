export function mergeByRevision<T extends { segmentId: string; revision: number; state: string }>(current: Record<string, T>, incoming: T[]): Record<string, T>;
export function transcriptDisplay(segment: any, accurateFinal?: any, postprocess?: any): { text: string; state: string; permanent: boolean };
export function translationDisplay(translation?: any, quality?: any): { text: string; state: string };
export function nearBottom(scrollTop: number, clientHeight: number, scrollHeight: number, threshold?: number): boolean;
export function workspaceStatus(value: { requesting?: boolean; reconnecting?: boolean; degraded?: boolean; error?: string; segmentCount: number }): "error" | "degraded" | "reconnecting" | "loading" | "ready" | "empty";
