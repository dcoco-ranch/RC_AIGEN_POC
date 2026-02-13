#!/bin/bash
# ====================================
#   ComfyUI Manager - Linux Setup
# ====================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"

cd "$APP_DIR"

echo "===================================="
echo "  ComfyUI Manager - Linux Setup"
echo "===================================="

# Check Python version
echo ""
echo "🐍 Checking Python..."
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
    PIP_CMD="pip3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
    PIP_CMD="pip"
else
    echo "❌ Python not found. Please install Python 3.9+"
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | grep -oP '\d+\.\d+')
echo "   Found Python $PYTHON_VERSION"

# Create virtual environment if it doesn't exist
echo ""
echo "📦 Setting up virtual environment..."
if [ ! -d "venv" ]; then
    $PYTHON_CMD -m venv venv
    echo "   Created virtual environment"
else
    echo "   Virtual environment already exists"
fi

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
echo ""
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo ""
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

# Create directories
echo ""
echo "📁 Creating directories..."
mkdir -p storage-models/models/{checkpoints,vae,loras,controlnet,clip,clip_vision,diffusion_models,text_encoders,unet,upscale_models,embeddings,hypernetworks,style_models,gligen,audio_encoders,diffusers,vae_approx,latent_upscale_models,photomaker,model_patches}
mkdir -p storage-user/{input,output,workflows}
mkdir -p logs

# Check for .env file
echo ""
echo "📝 Checking environment configuration..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "⚠️  Created .env from .env.example"
        echo "   Please edit .env with your configuration!"
    else
        echo "❌ No .env.example found!"
        exit 1
    fi
else
    echo "   .env file exists"
fi

echo ""
echo "===================================="
echo "  Setup Complete!"
echo "===================================="
echo ""
echo "Next steps:"
echo "1. Edit .env with your Supabase, Stripe, and GitLab credentials"
echo "2. Start the server: ./scripts/start.sh"
echo "3. Or manually: source venv/bin/activate && uvicorn app:app --host 0.0.0.0 --port 8730"
echo ""
echo "Access the portal at: http://localhost:8730"
echo ""
