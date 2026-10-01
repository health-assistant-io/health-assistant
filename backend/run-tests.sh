#!/bin/bash
# Test runner script for Health Assistant Backend

# Navigate to the backend directory
cd "$(dirname "$0")"

echo "==================================================="
echo "   Running Health Assistant Test Suite   "
echo "==================================================="

if [ "$1" == "--coverage" ] || [ "$1" == "-c" ]; then
    echo "Mode: Full Test Suite + Coverage Report"
    # Run tests and generate coverage report
    uv run pytest tests/ --cov=app --cov-report=term-missing --cov-report=html
    echo -e "\nDetailed HTML coverage report generated in: backend/htmlcov/index.html"
elif [ "$1" == "--watch" ] || [ "$1" == "-w" ]; then
    echo "Mode: Watch (requires pytest-watch)"
    uv run --with pytest-watch ptw tests/
elif [ -n "$1" ]; then
    echo "Mode: Specific file/directory ($1)"
    uv run pytest "tests/$1"
else
    echo "Mode: Full Test Suite"
    uv run pytest tests/
fi
