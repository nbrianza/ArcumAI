# Running ArcumAI with TLS (HTTPS and WSS)

Without TLS, the web UI runs over plain HTTP and the Outlook bridge over plain WebSocket (`ws://`). Anyone on the office network can then read login passwords, session cookies, the optional `X-Api-Key` of the add-in, email bodies, attachments and AI answers. This guide explains how to encrypt that traffic on Windows.

All commands below are for Windows PowerShell 5.1. Host names such as `arcumai.example.test` are examples: replace them with the name your users actually type.

## When do you need TLS?

| Setup | TLS needed? | What the server does at startup |
|---|---|---|
| Server and users on the same machine, `HOST=127.0.0.1` (or `localhost`, `::1`) | No | Starts as usual, no warning |
| Server reachable from other machines (`HOST=0.0.0.0` or a LAN address), no TLS | **Yes** | Starts, but logs one WARNING: `Transport is NOT encrypted ...` |
| `SSL_CERTFILE` and `SSL_KEYFILE` set (option A) | Done | Logs `Transport: https/wss` |
| A TLS reverse proxy in front of ArcumAI, `TLS_TERMINATED_UPSTREAM=true` (option B) | Done by the proxy | Logs `Transport: http/ws (TLS terminated upstream)` |

ArcumAI never refuses to start because TLS is missing; it warns. It does refuse to start when the TLS configuration is half done: only one of `SSL_CERTFILE` / `SSL_KEYFILE` set, or a path that does not exist. The error names the variable to fix.

## Where certificates come from

A TLS certificate proves to browsers and to the Outlook add-in that they talk to your ArcumAI server. The certificate must be issued for the host name the clients use (its Subject Alternative Name must contain, for example, `arcumai.example.test`), and every client machine must trust the authority that issued it.

1. **Internal CA (recommended for an office LAN).** If the office has a Windows domain with Active Directory Certificate Services, or an IT provider that runs a private certificate authority (CA), ask them for a server certificate for the ArcumAI host name. Domain-joined Windows machines usually already trust the internal CA through Group Policy, so no client step is needed.
2. **Public CA.** If the server has a public DNS name, a public CA (commercial, or a free ACME CA such as Let's Encrypt) can issue the certificate. Every Windows machine already trusts public CAs. Automatic renewal is not part of ArcumAI; plan it with your provider.
3. **Reverse proxy.** If the office already runs a reverse proxy (IIS with Application Request Routing, nginx, Caddy, a firewall appliance), the certificate lives on the proxy and ArcumAI itself serves plain HTTP behind it. See option B.
4. **Self-signed (test or lab only).** A self-signed certificate is fine to try TLS on one machine or in a lab. It is not suitable for production, because every client must import it by hand and nothing revokes it. See "Lab only: self-signed certificate" below.

You need two PEM files for ArcumAI: the certificate (optionally followed by the intermediate CA certificates, the "chain") and its **private key**. If you receive a `.pfx` / `.p12` file, convert it as shown in the lab section (step 3).

**Private keys are never committed.** Keep certificate and key files outside the ArcumAI folder (for example in `C:\ArcumAI-certs\`), never copy them into the repository, never attach them to issues or emails, and allow only the account that runs ArcumAI and administrators to read the key file.

## Option A: TLS directly in ArcumAI

1. Put the files outside the repository and restrict access to the key:

   ```powershell
   New-Item -ItemType Directory -Path C:\ArcumAI-certs -Force | Out-Null
   # copy arcumai-cert.pem and arcumai-key.pem into C:\ArcumAI-certs, then:
   icacls C:\ArcumAI-certs\arcumai-key.pem /inheritance:r /grant:r "${env:USERNAME}:R" "*S-1-5-32-544:F"
   ```

   `${env:USERNAME}` must be the account that runs ArcumAI; `*S-1-5-32-544` is the local Administrators group. After this, that account can only read the key: to replace or delete it later (for example at renewal), use an administrator PowerShell.

2. Set both paths in `.env`:

   ```ini
   SSL_CERTFILE=C:\ArcumAI-certs\arcumai-cert.pem
   SSL_KEYFILE=C:\ArcumAI-certs\arcumai-key.pem
   ALLOWED_ORIGINS=https://arcumai.example.test:8080
   ```

   The key must not be password protected (uvicorn reads it at startup without a prompt); this is why access to the file must be restricted. With TLS on, the browser origin starts with `https://`, so update `ALLOWED_ORIGINS` accordingly.

3. Start the server as usual (`.venv\Scripts\python main_nice.py`). The startup log shows `Transport: https/wss`. Open `https://arcumai.example.test:8080`. Both the web UI and the Outlook bridge (`wss://.../ws/outlook/<user>`) use the same port and the same certificate.

## Option B: behind a TLS reverse proxy

The proxy accepts `https://` and `wss://` from the clients, holds the certificate, and forwards the requests to ArcumAI over plain HTTP. The proxy must forward WebSocket upgrades (the web UI and `/ws/outlook/` both use WebSockets).

- **Proxy on the same machine (preferred).** Bind ArcumAI to loopback with `HOST=127.0.0.1` in `.env`. The cleartext hop never leaves the machine, and ArcumAI logs no warning; `TLS_TERMINATED_UPSTREAM` is not needed.
- **Proxy on another machine.** Set `TLS_TERMINATED_UPSTREAM=true` in `.env` to declare that TLS is terminated upstream; this silences the startup warning. Be aware that:
  - the hop between the proxy and ArcumAI is still cleartext, so keep it on a trusted, isolated network segment;
  - the flag is a declaration, not a check: ArcumAI cannot verify that a proxy exists;
  - uvicorn trusts `X-Forwarded-For` only from the addresses in the `FORWARDED_ALLOW_IPS` environment variable (default `127.0.0.1`). Set it to the proxy address, otherwise every client appears with the proxy IP and the per-IP login and WebSocket throttling can lock out all users at once.

In both cases set `ALLOWED_ORIGINS` to the `https://` origin the users open.

## Lab only: self-signed certificate

Use this only to test TLS. Run in Windows PowerShell 5.1; no administrator rights are needed except for step 4.

1. Create the certificate in your personal certificate store:

   ```powershell
   $cert = New-SelfSignedCertificate -DnsName arcumai.example.test -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(1)
   ```

2. Export it as a PFX (certificate plus private key, protected by a password you type) and as a `.cer` (public certificate only, for the clients):

   ```powershell
   New-Item -ItemType Directory -Path C:\ArcumAI-certs -Force | Out-Null
   $pfxPassword = Read-Host -AsSecureString -Prompt "PFX password"
   Export-PfxCertificate -Cert $cert -FilePath C:\ArcumAI-certs\arcumai.pfx -Password $pfxPassword
   Export-Certificate -Cert $cert -FilePath C:\ArcumAI-certs\arcumai.cer
   ```

3. Convert the PFX to the two PEM files ArcumAI reads. Save this script as `C:\ArcumAI-certs\pfx_to_pem.py` (it uses the `cryptography` package already installed with ArcumAI):

   ```python
   # Convert a PFX (PKCS#12) file into the PEM certificate and PEM private key
   # that ArcumAI expects in SSL_CERTFILE and SSL_KEYFILE.
   # Usage: .venv\Scripts\python pfx_to_pem.py <file.pfx> <cert.pem> <key.pem>
   import getpass
   import sys
   from pathlib import Path

   from cryptography.hazmat.primitives import serialization
   from cryptography.hazmat.primitives.serialization import pkcs12

   pfx_path, cert_path, key_path = (Path(p) for p in sys.argv[1:4])
   password = getpass.getpass("PFX password: ").encode("utf-8")
   key, cert, chain = pkcs12.load_key_and_certificates(pfx_path.read_bytes(), password)

   pem_chain = cert.public_bytes(serialization.Encoding.PEM)
   for extra in chain or []:
       pem_chain += extra.public_bytes(serialization.Encoding.PEM)
   cert_path.write_bytes(pem_chain)
   key_path.write_bytes(key.private_bytes(
       serialization.Encoding.PEM,
       serialization.PrivateFormat.PKCS8,
       serialization.NoEncryption(),
   ))
   print(f"Written {cert_path} and {key_path}")
   ```

   Run it from the ArcumAI folder:

   ```powershell
   .venv\Scripts\python C:\ArcumAI-certs\pfx_to_pem.py C:\ArcumAI-certs\arcumai.pfx C:\ArcumAI-certs\arcumai-cert.pem C:\ArcumAI-certs\arcumai-key.pem
   ```

   Then restrict access to the key and set `.env` as in option A. The same script works for a `.pfx` received from an internal or public CA.

4. Make each client machine trust the certificate (administrator PowerShell, on every machine that runs a browser or Outlook with the add-in):

   ```powershell
   Import-Certificate -FilePath C:\ArcumAI-certs\arcumai.cer -CertStoreLocation Cert:\LocalMachine\Root
   ```

   Copy only the `.cer` file to the clients, never the `.pfx` or the key. With a certificate from an internal CA, import the CA root certificate instead (usually already done by Group Policy); with a public CA, skip this step.

5. The host name must resolve on the clients: add `arcumai.example.test` to the office DNS (or, in a lab, to the hosts file of each client).

## Outlook add-in configuration for `wss://`

Edit `%APPDATA%\ArcumAI\Outlook\config.json` on each Outlook machine:

```json
{
  "ServerUrl": "wss://arcumai.example.test:8080",
  "UseSecureConnection": true
}
```

- `UseSecureConnection` must be `true` whenever the server host is not exactly `localhost`, a `127.x.x.x` address or `[::1]`. A host name that only contains "localhost" (for example `localhost.example.test`) is a remote host.
- The add-in validates the server certificate with the Windows certificate store. If the certificate is not trusted (step 4 skipped, wrong host name, expired), the connection fails and the add-in keeps retrying; check the add-in log. There is no option to skip certificate validation, and none should be added.
- Behind a reverse proxy, use the proxy address and port in `ServerUrl`.

## Checklist

- [ ] Certificate issued for the host name the clients use, trusted by every client.
- [ ] `SSL_CERTFILE` and `SSL_KEYFILE` set together (option A), or a reverse proxy with `HOST=127.0.0.1` or `TLS_TERMINATED_UPSTREAM=true` (option B).
- [ ] Startup log shows `Transport: https/wss` or `Transport: http/ws (TLS terminated upstream)` and no `Transport is NOT encrypted` warning.
- [ ] `ALLOWED_ORIGINS` uses the `https://` origin.
- [ ] Add-in `ServerUrl` starts with `wss://` and `UseSecureConnection` is `true`.
- [ ] Key file outside the repository, readable only by the ArcumAI account and administrators, never committed.
