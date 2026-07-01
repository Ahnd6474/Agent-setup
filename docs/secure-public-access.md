# Secure public access

The Agentic Local Server on `127.0.0.1:8765` is the end-user GUI and application
API. A named Cloudflare Tunnel publishes only that service. The exo panel and
API on `52415` are the monitoring and administration control plane; `52416` is
internal node transport. Both remain private.

## Cloudflare Tunnel

### Dashboard-managed tunnel token

For a tunnel created in the Cloudflare dashboard, store the connector token in
the macOS Keychain instead of the repository or a shell history:

```bash
read -rs TUNNEL_TOKEN
security add-generic-password -U \
  -a "$USER" \
  -s exo-cloudflare-tunnel-token \
  -w "$TUNNEL_TOKEN"
unset TUNNEL_TOKEN
```

Configure the tunnel's public hostname as `agent.example.com` and its HTTP
service as `http://127.0.0.1:8765`. Then run:

```bash
scripts/start_cloudflare_tunnel.sh
```

The script reads the token from the Keychain. A mode-`0600` token file can be
used instead by setting `CLOUDFLARE_TUNNEL_TOKEN_FILE`.

### Locally managed named tunnel

1. Add the domain to Cloudflare and install `cloudflared`.
2. Run `cloudflared tunnel login`.
3. Create a named tunnel:

   ```bash
   cloudflared tunnel create agentic-local
   ```

4. Copy `deploy/cloudflare/config.yml.example` to
   `~/.cloudflared/config.yml`, then set the tunnel UUID, credentials path,
   and hostname.
5. Create the DNS route:

   ```bash
   cloudflared tunnel route dns agentic-local agent.example.com
   ```

6. Copy `deploy/agentic/server.env.example` to
   `~/.agentic-local/server.env`, fill in the hostname, and set mode `0600`.
   Then start the Agent server:

   ```bash
   scripts/start_agent_server.sh
   ```

7. Run `scripts/start_cloudflare_tunnel.sh`.

Use a Cloudflare Access self-hosted application in front of the hostname as an
additional identity layer. Do not publish ports `52415`, `52416`, or `8080`.

## Google sign-in

Create a Google OAuth Web application and register this exact redirect URI:

```text
https://agent.example.com/auth/google/callback
```

Store the client secret only in node1's mode-`0600`
`~/.agentic-local/server.env`:

```bash
GOOGLE_OAUTH_CLIENT_ID=... \
GOOGLE_OAUTH_CLIENT_SECRET=... \
GOOGLE_OAUTH_REDIRECT_URI=https://agent.example.com/auth/google/callback \
...
```

The server validates a single-use OAuth state, exchanges the authorization
code on node1, requires a verified Google email, and stores only the stable
Google subject and email. OAuth access and refresh tokens are not persisted.
