#!/usr/bin/env bash

# Telegram Migrator - Setup Script

set -e

echo "========================================="
echo "  Telegram Migrator Setup"
echo "========================================="
echo ""

# Check Python version
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | awk '{print $2}')
required_version="3.8"

if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" 2>/dev/null; then
    echo "❌ Error: Python 3.8 or higher is required"
    echo "   Current version: $python_version"
    exit 1
fi
echo "✓ Python version OK: $python_version"
echo ""

# Create virtual environment (optional but recommended)
echo "Do you want to create a virtual environment? (recommended)"
read -p "Create venv? [Y/n]: " create_venv
create_venv=${create_venv:-Y}

if [[ $create_venv =~ ^[Yy]$ ]]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    
    echo "Activating virtual environment..."
    source venv/bin/activate
    
    echo "✓ Virtual environment created"
    echo "  To activate later: source venv/bin/activate"
    echo ""
fi

# Install package + dependencies
echo "Installing package and dependencies..."
pip install -e .

echo ""
echo "✓ Package installed"
echo ""


echo ""
echo "========================================="
echo "  Setup Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. Get API credentials from https://my.telegram.org/apps"
echo "2. Copy .env.example to .env and fill in your credentials"
echo "   OR provide them via command-line arguments"
echo "3. Run: telegram-migrator dump -c @channelname -o ./backup"
echo ""
echo "For help: telegram-migrator --help"
echo "See README.md for full documentation"
echo ""
