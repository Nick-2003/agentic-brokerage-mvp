// Browser client for the token-free public brokerage connection API (089).
// SnapTrade application credentials and per-user userSecret never enter this module.

export type BrokerConnection = {
  id: string;
  provider: 'snaptrade' | 'ibkr_flex';
  status: 'pending' | 'active' | 'disabled' | 'error' | 'revoked';
  last_error_code?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type BrokerAccount = {
  id: string;
  connection_id: string;
  masked_name: string;
  base_currency: string;
  is_selected: boolean;
  status: 'active' | 'disabled' | 'revoked';
  created_at?: string;
  updated_at?: string;
};

export type BrokerageState = {
  connections: BrokerConnection[];
  accounts: BrokerAccount[];
};

const PERSONALIZED_IBKR_NAME = /^(?:Interactive Brokers|IBKR)\s*\(/i;

export function brokerageAccountDisplayName(name: string): string {
  const normalized = name.trim();
  return PERSONALIZED_IBKR_NAME.test(normalized)
    ? 'Interactive Brokers'
    : normalized || 'Brokerage account';
}

type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status: number };

const ERROR_CODE = /^[a-z0-9_]{1,80}$/;

function safeError(value: unknown, status: number): string {
  return typeof value === 'string' && ERROR_CODE.test(value)
    ? value
    : `brokerage_http_${status}`;
}

async function request<T>(
  path: string,
  token: string,
  init?: RequestInit
): Promise<ApiResult<T>> {
  try {
    const response = await fetch(path, {
      ...init,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
      cache: 'no-store',
    });
    const body = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    } & T;
    if (!response.ok) {
      return {
        ok: false,
        error: safeError(body.detail, response.status),
        status: response.status,
      };
    }
    return { ok: true, data: body };
  } catch {
    return { ok: false, error: 'brokerage_network_error', status: 0 };
  }
}

export async function getBrokerageState(token: string): Promise<ApiResult<BrokerageState>> {
  return request<BrokerageState>('/api/broker-connections', token);
}

export async function createSnapTradeSession(
  token: string
): Promise<ApiResult<{ portal_url: string; expires_in_seconds: number }>> {
  const result = await request<{ portal_url: string; expires_in_seconds: number }>(
    '/api/broker-connections/snaptrade/session',
    token,
    { method: 'POST', body: '{}' }
  );
  if (!result.ok) return result;
  try {
    const url = new URL(result.data.portal_url);
    if (url.protocol !== 'https:') throw new Error('not https');
  } catch {
    return { ok: false, error: 'snaptrade_portal_url_invalid', status: 502 };
  }
  return result;
}

export async function verifySnapTradeConnection(
  token: string,
  externalConnectionId: string
): Promise<ApiResult<BrokerageState>> {
  return request<BrokerageState>('/api/broker-connections/snaptrade/verify', token, {
    method: 'POST',
    body: JSON.stringify({ external_connection_id: externalConnectionId }),
  });
}

export async function selectBrokerAccount(
  token: string,
  accountId: string
): Promise<ApiResult<{ selected_account_id: string; state: BrokerageState }>> {
  return request<{ selected_account_id: string; state: BrokerageState }>(
    '/api/broker-accounts/select',
    token,
    {
      method: 'POST',
      body: JSON.stringify({ account_id: accountId }),
    }
  );
}

const FRIENDLY_ERRORS: Record<string, string> = {
  authentication_required: 'Sign in again before managing brokerage connections.',
  snaptrade_not_configured: 'SnapTrade is not configured on the backend yet.',
  snaptrade_sdk_missing: 'The SnapTrade backend dependency is unavailable.',
  snaptrade_timeout: 'SnapTrade took too long to respond. Please try again.',
  snaptrade_rate_limited: 'SnapTrade is temporarily rate-limiting requests. Try again shortly.',
  snaptrade_user_already_exists:
    'This SnapTrade profile already exists but its saved credentials are missing. Contact support before retrying.',
  snaptrade_registration_recovery_required:
    'SnapTrade registration needs manual recovery. Contact support before retrying.',
  broker_connection_store_failed:
    'The brokerage profile could not be saved. Wait a moment, then try again.',
  snaptrade_sync_in_progress: 'Your brokerage is still syncing. Retry in a moment.',
  snaptrade_accounts_not_ready: 'Your accounts are still syncing. Retry in a moment.',
  snaptrade_connection_disabled: 'This connection needs to be reconnected.',
  snaptrade_connection_not_verified: 'SnapTrade could not verify that connection.',
  snaptrade_callback_invalid: 'SnapTrade did not return a valid successful connection.',
  broker_account_currency_missing: 'The account currency is not available yet.',
  broker_account_not_found: 'That account is no longer available.',
  brokerage_network_error: 'Could not reach the brokerage service. Check your connection.',
  snaptrade_portal_url_invalid: 'The backend returned an invalid connection link.',
};

export function brokerageErrorMessage(code: string): string {
  return FRIENDLY_ERRORS[code] ?? 'Could not complete the brokerage request.';
}
