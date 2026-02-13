# RC_AIGEN_POC

**Ranch Computing AI Generation Platform - Proof of Concept**

A SaaS platform for monetizing ComfyUI with GPU compute, featuring Ranch Cloud Credits (RCC) for usage-based billing.

## Overview

This POC demonstrates a hybrid monetization model for ComfyUI:

- **Subscriptions** - Monthly/annual plans with included RCC credits
- **Top-ups** - On-demand credit purchases via Stripe
- **Pay-per-task** - Image generation (1 RCC) / Video generation (5 RCC)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        User Browser                         │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              ComfyUI Manager Portal (FastAPI)               │
│  • Authentication (JWT + GitLab OAuth)                      │
│  • RCC Wallet & Ledger                                      │
│  • Stripe Integration                                       │
│  • Admin Dashboard                                          │
│                      Port 8730                              │
└─────────────────────────┬───────────────────────────────────┘
                          │ Docker Network
┌─────────────────────────▼───────────────────────────────────┐
│                  ComfyUI (GPU Container)                    │
│  • Diffusion Models                                         │
│  • Custom Nodes                                             │
│  • Workflow Execution                                       │
│                      Port 8188                              │
└─────────────────────────────────────────────────────────────┘
```

## Project Structure

```
RC_AIGEN_POC/
├── README.md           # This file
├── PRD.md              # Product Requirements Document (French)
├── DEMO.md             # Demo script
└── comfyui-manager/    # Main application
    ├── app.py          # FastAPI portal
    ├── docker-compose-*.yml
    ├── storage-models/ # AI models (gitignored)
    ├── storage-user/   # User data (gitignored)
    └── templates/      # HTML templates
```

## Quick Start

```powershell
cd comfyui-manager

# Setup environment
.\setup.bat

# Configure (edit .env with your credentials)
copy .env.example .env

# Start with Docker
.\scripts\docker_start.bat
```

**Access:**

- Portal: <http://localhost:8000>
- ComfyUI: <http://localhost:8188>

## Requirements

- Windows 11 with WSL2
- Docker Desktop with GPU support
- NVIDIA GPU with CUDA drivers
- Python 3.11+

## Documentation

- [ComfyUI Manager README](comfyui-manager/README.md) - Detailed setup and API docs
- [PRD.md](PRD.md) - Product Requirements Document
- [DEMO.md](DEMO.md) - Demo walkthrough

## Tech Stack

- **Backend:** FastAPI, SQLAlchemy, Supabase
- **Frontend:** Jinja2 Templates, Bootstrap
- **Payments:** Stripe (Checkout + Webhooks)
- **Auth:** JWT, GitLab OAuth (for admins)
- **AI Runtime:** ComfyUI in Docker with NVIDIA GPU

## License

Proprietary - Ranch Computing © 2026
