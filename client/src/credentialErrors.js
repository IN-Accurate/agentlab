export function isCredentialExpired(error) {
  return (error?.error?.code ?? error?.code) === 'credential_expired';
}
