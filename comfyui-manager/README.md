# ComfyUI Manager

**Ranch Cloud Credits (RCC) powered ComfyUI SaaS Portal**

A complete solution for hosting and monetizing ComfyUI with Docker, featuring:

- 🔐 User authentication (JWT + GitLab OAuth for admins)
- 💰 RCC (Ranch Cloud Credits) wallet system with full audit ledger
- 💳 Stripe integration for top-ups and subscriptions
- 🖼️ Job management (Image: 1 RCC, Video: 5 RCC)
- 🛡️ Admin dashboard with KPIs, user management, and model management
- 🐳 Docker-based ComfyUI with GPU support

## Quick Start

### Prerequisites

- Windows 11 with WSL2
- Docker Desktop with GPU support
- Python 3.11+
- NVIDIA GPU with drivers

### Installation (Local Development)

1. **Clone and setup:**

   ```powershell
   cd d:\DOM\DEVS\RC_AIGEN_POC\comfyui-manager
   .\setup.bat
   ```

2. **Configure environment:**

   ```powershell
   copy .env.example .env
   # Edit .env with your credentials
   ```

3. **Start the portal:**

   ```powershell
   uvicorn app:app --host 0.0.0.0 --port 8000 --reload
   ```

4. **Start ComfyUI (optional - requires GPU):**

   ```powershell
   docker compose -f docker-compose-comfyui.yml up -d
   ```

5. **Access:**
   - Portal: <http://localhost:8000>
   - ComfyUI: <http://localhost:8188>

### Docker Deployment (Production)

Both the Portal and ComfyUI run in separate containers for isolation and security.

1. **Create the shared network:**

   ```bash
   docker network create comfyui-network
   ```

2. **Start ComfyUI (GPU container):**

   ```bash
   docker compose -f docker-compose-comfyui.yml up -d
   ```

3. **Start the Portal:**

   ```bash
   docker compose -f docker-compose-portal.yml up -d --build
   ```

4. **Access:**
   - Portal: <http://localhost:8000>
   - ComfyUI: <http://localhost:8188>

5. **View logs:**

   ```bash
   # Portal logs
   docker compose -f docker-compose-portal.yml logs -f
   
   # ComfyUI logs
   docker compose -f docker-compose-comfyui.yml logs -f
   ```

6. **Stop services:**

   ```bash
   docker compose -f docker-compose-portal.yml down
   docker compose -f docker-compose-comfyui.yml down
   ```

### Quick Scripts (Windows)

```powershell
# Start entire stack
.\scripts\docker_start.bat

# Stop entire stack
.\scripts\docker_stop.bat
```

## Configuration

### Environment Variables

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase anon key |
| `SECRET_KEY` | JWT secret key |
| `GITLAB_CLIENT_ID` | GitLab OAuth app ID |
| `GITLAB_CLIENT_SECRET` | GitLab OAuth secret |
| `STRIPE_SECRET_KEY` | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook secret |

### Database Setup (Supabase)

Run the SQL in `supabase_schema.sql` in your Supabase SQL editor.

### GitLab OAuth Setup

For self-hosted GitLab (e.g., `gitlab.ranchcomputing.com`):

1. Go to your GitLab instance: `https://gitlab.ranchcomputing.com/-/profile/applications`
2. Create a new application:
   - Name: `ComfyUI Manager`
   - Redirect URI: `http://localhost:8000/auth/gitlab/callback`
   - Scopes: `read_user`
3. Copy the Application ID and Secret to `.env`
4. Set `GITLAB_BASE_URL=https://gitlab.ranchcomputing.com` in `.env`

### Stripe Setup

1. Create a Stripe account at <https://stripe.com>
2. Get your API keys from the Dashboard
3. Set up webhook endpoint: `http://your-domain/webhooks/stripe`
4. Listen for events: `checkout.session.completed`, `invoice.paid`

## API Endpoints

### Authentication

- `POST /auth/register` - Register new user
- `POST /auth/login` - Login with email/password
- `GET /auth/gitlab` - GitLab OAuth (admin)
- `GET /auth/logout` - Logout

### User

- `GET /me` - Get profile with RCC balance
- `GET /wallet/balance` - Get RCC balance
- `GET /wallet/history` - Get RCC transaction history

### Jobs

- `POST /jobs` - Create a new job
- `GET /jobs` - List user's jobs
- `GET /jobs/{id}` - Get job details
- `PATCH /jobs/{id}/status` - Update job status

### Payments

- `POST /checkout/topup` - Create top-up checkout
- `POST /checkout/subscription` - Create subscription checkout
- `POST /webhooks/stripe` - Stripe webhook handler

### Admin

- `GET /admin/dashboard` - Admin dashboard
- `GET /admin/users` - List users
- `POST /admin/users/{id}/adjust-rcc` - Adjust user RCC
- `GET /admin/jobs` - List all jobs
- `GET /admin/models` - List models
- `POST /admin/models/install` - Install model from URL
- `POST /admin/comfyui/start|stop|restart` - Control ComfyUI

## RCC Pricing (V1)

| Task Type | Cost |
|-----------|------|
| Image Task | 1 RCC |
| Video Task | 5 RCC |

## Architecture

```
┌─────────────────┐     ┌──────────────┐
│   User/Admin    │────▶│   FastAPI    │
└─────────────────┘     │   Portal     │
                        └──────┬───────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
        ▼                      ▼                      ▼
┌───────────────┐    ┌─────────────────┐    ┌────────────────┐
│   Supabase    │    │     Stripe      │    │    ComfyUI     │
│   (Database)  │    │   (Payments)    │    │    (Docker)    │
└───────────────┘    └─────────────────┘    └────────────────┘
```

## License

Copyright © 2026 Ranch Computing
