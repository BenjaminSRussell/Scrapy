@echo off
setlocal

echo "🚀 Setting up and running UConn Web Scraping Pipeline..."

REM Change to the project directory
cd /d "%~dp0"

REM Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo "❌ Python is not installed or not in the PATH."
    exit /b 1
)

REM Create and activate virtual environment
if not exist ".venv" (
    echo "🐍 Creating virtual environment..."
    python -m venv .venv
)

echo "✅ Activating virtual environment..."
call .venv\Scripts\activate.bat

REM Install pip dependencies
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

REM Install spaCy language model
echo "🧠 Installing spaCy language model..."
python -m spacy download en_core_web_sm

REM Create data directories
echo "📁 Creating data directories..."
mkdir data\raw data\processed\stage01 data\processed\stage02 data\processed\stage03 data\logs data\cache data\exports data\temp data\checkpoints >nul 2>&1

echo "✅ Setup complete. Running the pipeline..."
echo "---"

REM Run the discovery spider
scrapy crawl discovery

echo "---"
echo "🎉 Pipeline execution finished."
endlocal
