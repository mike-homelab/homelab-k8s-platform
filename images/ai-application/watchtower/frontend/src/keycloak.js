import Keycloak from 'keycloak-js'

// Keycloak OIDC configuration for Watchtower
// SSO issuer: https://sso.michaelhomelab.work/realms/homelab
const keycloak = new Keycloak({
  url: 'https://sso.michaelhomelab.work',
  realm: 'homelab',
  clientId: 'watchtower',
})

export default keycloak
