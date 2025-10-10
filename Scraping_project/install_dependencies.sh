#!/bin/bash
# Install Missing Dependencies for Scraping Pipeline

set -e  # Exit on error

echo "================================================================================"
echo "INSTALLING MISSING DEPENDENCIES"
echo "================================================================================"
echo ""

# Check if we're in a virtual environment
if [[ -z "$VIRTUAL_ENV" ]]; then
    echo "⚠️  WARNING: Not in a virtual environment"
    echo ""
    echo "It's recommended to use a virtual environment:"
    echo "  python3 -m venv venv"
    echo "  source venv/bin/activate"
    echo "  ./install_dependencies.sh"
    echo ""
    read -p "Continue with system Python? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "❌ Installation cancelled"
        exit 1
    fi
    INSTALL_CMD="pip install --user"
else
    echo "✅ Virtual environment detected: $VIRTUAL_ENV"
    INSTALL_CMD="pip install"
fi

echo ""
echo "Installing missing dependencies..."
echo ""

# Install the three critical missing packages
echo "📦 Installing pandas..."
$INSTALL_CMD pandas>=2.0.0

echo ""
echo "📦 Installing datasketch..."
$INSTALL_CMD datasketch>=1.6.0


echo ""
echo "================================================================================"
echo "✅ INSTALLATION COMPLETE"
echo "================================================================================"
echo ""

# Verify installations
echo "Verifying installations..."
python3 << 'EOF'
import sys

packages = [
    ('pandas', 'Pandas'),
    ('datasketch', 'DataSketch'),
]

all_ok = True
for module, name in packages:
    try:
        __import__(module)
        print(f"  ✅ {name}")
    except ImportError:
        print(f"  ❌ {name} - Failed to import")
        all_ok = False

if all_ok:
    print("\n✅ All packages installed successfully!")
    sys.exit(0)
else:
    print("\n❌ Some packages failed to install")
    sys.exit(1)
EOF

echo ""
echo "================================================================================"
echo "NEXT STEPS"
echo "================================================================================"
echo ""
echo "1. Run tests to verify:"
echo "   pytest tests/ -v"
echo ""
echo "2. Drain and run pipeline:"
echo "   python cli.py drain"
echo "   python cli.py pipeline"
echo ""
echo "3. Export results:"
echo "   python cli.py export --output exports"
echo ""
