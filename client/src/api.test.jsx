import { afterEach, expect, it, vi } from 'vitest';
import { proposeFix } from './api.js';

afterEach(() => vi.unstubAllGlobals());

it('retains the saved diagnosis and credential error code when starting a repair fails', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'diagnosis' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'credential_expired', message: 'Credential expired' } }), { status: 404 })));
  const result = await proposeFix('result', 'key');
  expect(result).toEqual({ id: 'diagnosis', repairError: 'Credential expired', repairErrorCode: 'credential_expired' });
});
