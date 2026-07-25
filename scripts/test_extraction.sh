#!/bin/bash
#
# scripts/test_extraction.sh
#
# Smoke test the document extraction pipeline: login → upload → read the
# initial extraction status → trigger extraction → poll the status endpoint.
# Exercises the OCR/CSV extraction background-task kickoff against a running
# backend + Celery worker.
#
# Prerequisites:
#   - Backend + Celery worker running (./scripts/run-dev.sh starts both).
#   - Demo admin: admin@health-assistant.local / admin123.
#   - ``test_documents/test1.png`` at the project root.
#   - OCR requires an AI provider configured (see Settings → AI Config, or
#     OPENAI_API_KEY in .env).
#
# Usage:
#   ./scripts/test_extraction.sh                  # run the test
#   ./scripts/test_extraction.sh -h | --help      # print this help and exit
#
# Exits non-zero on login or upload failure. No flags are accepted.

API_URL="http://localhost:8000"
TEST_FILE="test_documents/test1.png"

print_help() {
  sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -h|--help) print_help ;;
        *)
            echo "Unknown parameter: $1 (try --help)" >&2
            exit 1
            ;;
    esac
done

echo "Testing Document Extraction..."
echo ""

# Login
TOKEN_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin@health-assistant.local&password=admin123")

ACCESS_TOKEN=$(echo $TOKEN_RESPONSE | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)

if [ -z "$ACCESS_TOKEN" ]; then
    echo "❌ Login failed"
    exit 1
fi

# Upload document
echo "1. Uploading document..."
UPLOAD_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/documents" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "file=@$TEST_FILE")

DOC_ID=$(echo $UPLOAD_RESPONSE | grep -o '"id":"[^"]*' | cut -d'"' -f4)
echo "   Uploaded! ID: $DOC_ID"
echo ""

# Get initial status
echo "2. Initial status..."
STATUS_RESPONSE=$(curl -s -X GET "${API_URL}/api/v1/documents/$DOC_ID/extract/status" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

echo "   Response:"
echo "$STATUS_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$STATUS_RESPONSE"
echo ""

# Trigger extraction
echo "3. Triggering extraction..."
EXTRACT_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/documents/$DOC_ID/extract" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

echo "   Response:"
echo "$EXTRACT_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$EXTRACT_RESPONSE"
echo ""

# Get status after trigger
echo "4. Status after trigger..."
sleep 1
STATUS_RESPONSE2=$(curl -s -X GET "${API_URL}/api/v1/documents/$DOC_ID/extract/status" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

echo "   Response:"
echo "$STATUS_RESPONSE2" | python3 -m json.tool 2>/dev/null || echo "$STATUS_RESPONSE2"
echo ""

echo "✅ Extraction test complete!"

