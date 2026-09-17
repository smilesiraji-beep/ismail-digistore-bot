# Ismail Digistore — Master v0.8 Deployment Ready

USD-only Telegram digital-subscription reseller bot.

## Added in v0.8
- Free/paid-host deployment files: `start.sh`, `Procfile`, `runtime.txt`
- Lightweight HTTP health endpoint on the host-provided `PORT`
- Persistent SQLite path support through `DB_PATH`
- Safe SQLite backup utility: `python backup_db.py`
- Hardened `.gitignore` so tokens, API keys and DB/backups are not committed
- Deployment checklist below

## Existing systems retained
VenteBot live products/stock/quote/order/status, custom USD selling prices, activation identifiers, manual payment approval, delivery/status, referral campaigns (free or discounted reward), USD offers, support settings, broadcast, statistics and SQLite persistence.

## Local test
1. Install Python 3.12+.
2. Run `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env`.
4. Fill `BOT_TOKEN`, `ADMIN_IDS`, `VENTE_BASE_URL`, and `VENTE_API_KEY` yourself. Never send those secrets in chat.
5. For local use change `DB_PATH` to `digistore.db` or another writable path.
6. Run `python bot.py`.

## Hosting deployment
Upload this project to a Python-capable host. Configure these environment variables in the host dashboard: `BOT_TOKEN`, `ADMIN_IDS`, `VENTE_BASE_URL`, `VENTE_API_KEY`, `VENTE_TIMEOUT`, `DB_PATH`, and (if required by the host) `PORT`.

Start command: `./start.sh`

The bot uses Telegram long polling. The small HTTP health service exists only so platforms expecting a web process can see that the deployment is alive.

### Important database rule
Free hosts can have ephemeral filesystems. If the provider supports a persistent disk/volume, mount it at `/data` and keep `DB_PATH=/data/digistore.db`. Without persistent storage, redeploy/restart may lose SQLite data. Before moving hosts, make a database backup and transfer it with the project.

## Database backup
Run `python backup_db.py`. It creates a timestamped, transaction-safe SQLite copy in `backups/` by default. Keep backups private because order/customer records can be sensitive.

## Security
Never put real BotFather tokens or VenteBot API keys in the ZIP, GitHub repository, screenshots, or public messages. Set them only as private environment variables on the deployment platform. If a token/key is exposed, rotate it immediately.

## Production note
SQLite is suitable for the initial/small deployment. For larger traffic or multiple bot instances, migrate to PostgreSQL before scaling horizontally.
