# Digital Ocean Deployment Guide

Deploy SuperStrat trading bot with Grafana monitoring to a Digital Ocean droplet.

## Prerequisites

- Digital Ocean droplet (Ubuntu 22.04, minimum 2GB RAM recommended)
- SSH access to the droplet
- Schwab API credentials (app key + secret)

## Quick Deploy (Automated)

```bash
# 1. Create your .env file
cp deploy/.env.example deploy/.env
# Edit deploy/.env with your credentials

# 2. Run deploy script
./deploy/deploy.sh <your-droplet-ip>
```

## Manual Deployment

### Option A: Native Bot + Docker Monitoring (Recommended)

This runs the bot natively (faster startup, easier debugging) with Docker only for monitoring.

```bash
# SSH into your droplet
ssh root@<droplet-ip>

# Install dependencies
apt update && apt install -y python3.11 python3.11-venv python3-pip docker.io docker-compose-v2

# Clone/upload your code
cd /opt
git clone <your-repo> superstrat
cd superstrat

# Setup Python environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .

# Create environment file
cat > .env << 'EOF'
SCHWAB_APP_KEY=your_key
SCHWAB_APP_SECRET=your_secret
EOF

# Authenticate with Schwab (one-time)
python scripts/schwab_auth.py

# Start monitoring stack
cd monitoring
docker compose up -d

# Run the bot (in screen/tmux for persistence)
screen -S trading
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
TIMEFRAME=1day python examples/schwab_superstrat_live.py 2>&1 | tee logs/strat_output.log
# Ctrl+A, D to detach
```

### Option B: Full Docker Deployment

```bash
# SSH into your droplet
ssh root@<droplet-ip>

# Install Docker
curl -fsSL https://get.docker.com | sh

# Create app directory
mkdir -p /opt/superstrat
cd /opt/superstrat

# Upload deploy folder contents (from local machine)
# scp -r deploy/* root@<droplet-ip>:/opt/superstrat/

# Create .env file
cp .env.example .env
nano .env  # Edit with your credentials

# Build and start
docker compose up -d --build
```

## Accessing Grafana

- URL: `http://<droplet-ip>:3000`
- Username: `admin` (or your GRAFANA_USER)
- Password: Your GRAFANA_PASSWORD from .env

### Useful LogQL Queries

```logql
# All logs
{job="superstrat"}

# Trading signals
{job="superstrat"} |~ ">>> SIGNAL"

# Bar data
{job="superstrat"} |~ "Bar received"

# Errors and warnings
{job="superstrat"} |~ "ERROR|WARN"
```

## Systemd Service (Optional)

To run the bot as a system service:

```bash
cat > /etc/systemd/system/superstrat.service << 'EOF'
[Unit]
Description=SuperStrat Trading Bot
After=network.target docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/superstrat
EnvironmentFile=/opt/superstrat/.env
ExecStart=/opt/superstrat/.venv/bin/python examples/schwab_superstrat_live.py
Restart=always
RestartSec=10
StandardOutput=append:/opt/superstrat/logs/strat_output.log
StandardError=append:/opt/superstrat/logs/strat_output.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable superstrat
systemctl start superstrat
```

## Security Notes

1. **Firewall**: Only expose port 3000 (Grafana) publicly
   ```bash
   ufw allow 22    # SSH
   ufw allow 3000  # Grafana
   ufw enable
   ```

2. **Change Grafana password** from default!

3. **Optional**: Add nginx reverse proxy with HTTPS
   ```bash
   apt install nginx certbot python3-certbot-nginx
   # Configure nginx for grafana proxy
   certbot --nginx -d your-domain.com
   ```

## Monitoring Logs

```bash
# View bot logs
tail -f /opt/superstrat/logs/strat_output.log

# View container logs
docker logs -f trading-bot
docker logs -f promtail
docker logs -f loki
docker logs -f grafana
```

## Troubleshooting

### Logs not appearing in Grafana
1. Check Promtail is running: `docker logs promtail`
2. Verify log file exists: `ls -la /opt/superstrat/logs/`
3. Check Loki health: `curl http://localhost:3100/ready`

### Bot not connecting to Schwab
1. Re-authenticate: `python scripts/schwab_auth.py`
2. Check token status: `make schwab-status`
3. Tokens expire after 7 days - re-auth required
