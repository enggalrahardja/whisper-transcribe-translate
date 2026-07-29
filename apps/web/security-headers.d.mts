export type SecurityHeader = Readonly<{ key: string; value: string }>;

export function contentSecurityPolicy(environment?: string): string;
export function securityHeaders(environment?: string): SecurityHeader[];
