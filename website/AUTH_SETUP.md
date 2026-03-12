# imagic Auth Setup

This project now supports:

- Local account sign-up and sign-in for the web app
- Google OAuth sign-in for the web app
- Credit-pack purchases for the web app
- Redeemable web credit keys
- Desktop license activation against the web API

This file covers what still needs to be done manually.

## 1. Google Cloud Setup

Create or use an existing Google Cloud project.

1. Open Google Cloud Console.
2. Go to APIs & Services.
3. Configure the OAuth consent screen.
4. Set app type to External unless you specifically want internal-only access.
5. Add your production domain to Authorized domains.
6. Create OAuth 2.0 Client ID credentials.
7. Choose Web application.
8. Add these Authorized redirect URIs:
   - https://imagic-ink.fly.dev/api/auth/google/callback
   - https://YOUR-CUSTOM-DOMAIN/api/auth/google/callback
   - http://localhost:8000/api/auth/google/callback
9. Copy the generated client ID and client secret.

## 2. Fly Secrets

Set these secrets before deploying.

Example commands:

```powershell
flyctl secrets set IMAGIC_BASE_URL="https://imagic-ink.fly.dev"
flyctl secrets set IMAGIC_SESSION_SECRET="REPLACE_WITH_A_LONG_RANDOM_SECRET"
flyctl secrets set GOOGLE_CLIENT_ID="REPLACE_WITH_GOOGLE_CLIENT_ID"
flyctl secrets set GOOGLE_CLIENT_SECRET="REPLACE_WITH_GOOGLE_CLIENT_SECRET"
flyctl secrets set STRIPE_SECRET_KEY="REPLACE_WITH_STRIPE_SECRET_KEY"
flyctl secrets set STRIPE_WEBHOOK_SECRET="REPLACE_WITH_STRIPE_WEBHOOK_SECRET"
flyctl secrets set STRIPE_PRICE_ID="REPLACE_WITH_STRIPE_PRICE_ID"
flyctl secrets set IMAGIC_ADMIN_API_KEY="REPLACE_WITH_A_LONG_RANDOM_ADMIN_KEY"
```

Optional explicit cookie/redirect overrides:

```powershell
flyctl secrets set IMAGIC_COOKIE_SECURE="true"
flyctl secrets set GOOGLE_REDIRECT_URL="https://imagic-ink.fly.dev/api/auth/google/callback"
```

## 3. Stripe Setup

Use Stripe for the web credit pack.

1. Create a product for web credits.
2. Create a one-time price for the pack.
   Suggested starting point: $5 for 500 images.
3. Copy the resulting `price_...` ID into `STRIPE_PRICE_ID`.
4. Add a webhook endpoint:
   - https://imagic-ink.fly.dev/api/stripe/webhook
5. Subscribe the webhook to:
   - checkout.session.completed
6. Copy the webhook signing secret into `STRIPE_WEBHOOK_SECRET`.

## 4. Issue Keys Manually

Admin-issued keys are currently generated through the API.

Desktop license key example:

```powershell
$headers = @{ "x-admin-key" = "REPLACE_WITH_IMAGIC_ADMIN_API_KEY"; "Content-Type" = "application/json" }
$body = '{"product_type":"desktop"}'
Invoke-RestMethod -Method POST -Uri "https://imagic-ink.fly.dev/api/admin/licenses/issue" -Headers $headers -Body $body
```

Web credit key example:

```powershell
$headers = @{ "x-admin-key" = "REPLACE_WITH_IMAGIC_ADMIN_API_KEY"; "Content-Type" = "application/json" }
$body = '{"product_type":"web_credit","credits_total":500}'
Invoke-RestMethod -Method POST -Uri "https://imagic-ink.fly.dev/api/admin/licenses/issue" -Headers $headers -Body $body
```

## 5. Desktop Activation Config

To enable the desktop activation gate, set these values in the desktop config:

```yaml
security:
  require_activation: true
  license_api_base_url: "https://imagic-ink.fly.dev"
```

On first launch after that, the desktop app will prompt for:

- account email
- account password
- desktop license key

The app then stores a server-issued activation token locally.

## 6. Deploy

Once the secrets are configured:

```powershell
flyctl deploy
```

Then verify:

1. Landing page loads new pricing and parallax images.
2. `/app` shows local sign-in buttons and a Google button.
3. Google sign-in returns to `/app` successfully.
4. Credit purchase redirects to Stripe Checkout.
5. Stripe webhook credits the signed-in account.
6. Desktop activation succeeds against the production API.

## 7. Current Limitations

- Local sign-in UI still uses browser prompts instead of proper forms.
- The web account store currently uses a local SQLite database under `website/data/accounts.db`.
- Admin key issuance is a raw API call; there is no admin dashboard yet.
- Desktop protection is server-backed, but no desktop client-side protection is absolute.