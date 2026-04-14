import Keycloak from 'keycloak-js'

// Keycloak OIDC configuration for Savant
// SSO issuer: https://sso.michaelhomelab.work/realms/homelab
const keycloak = new Keycloak({
  url: 'https://sso.michaelhomelab.work',
  realm: 'homelab',
  clientId: 'savant',
})

export default keycloak
