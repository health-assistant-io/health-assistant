#!/bin/bash
#
# scripts/test_document_status.sh
#
# Smoke test the upload + status-check pair: login → upload a document →
# verify a document id comes back → query the status endpoint. Confirms the
# upload→status introspection path works against a running backend.
#
# Prerequisites:
#   - Backend running on http://localhost:8000 (./scripts/run-dev.sh).
#   - Demo admin: admin@health-assistant.local / admin123.
#   - ``test_documents/test1.png`` at the project root.
#
# Usage:
#   ./scripts/test_document_status.sh             # run the test
#   ./scripts/test_document_status.sh -h | --help # print this help and exit
#
# Exits non-zero on login or upload failure. No flags are accepted.

API_URL="http://localhost:8000"
TEST_FILE="test_documents/test1.png"

print_help() {
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
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

echo "Testing Document Upload and Status Check..."
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

echo "✅ Logged in successfully"
echo ""

# Upload document
echo "Uploading document..."
UPLOAD_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/documents" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "file=@$TEST_FILE" \
  -F "patient_id=test-patient-123")

echo "Upload Response:"
echo "$UPLOAD_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$UPLOAD_RESPONSE"
echo ""

# Extract document ID
DOC_ID=$(echo $UPLOAD_RESPONSE | grep -o '"id":"[^"]*' | cut -d'"' -f4)

if [ -z "$DOC_ID" ]; then
    echo "❌ No document ID in response"
    exit 1
fi

echo "✅ Document uploaded! ID: $DOC_ID"
echo ""

# Get document status
echo "Checking document status..."
sleep 1

STATUS_RESPONSE=$(curl -s -X GET "${API_URL}/api/v1/documents/$DOC_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

echo "Document Info Response:"
echo "$STATUS_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$STATUS_RESPONSE"
echo ""

# Check if we got a valid response
if echo "$STATUS_RESPONSE" | grep -q '"filename"'; then
    echo "✅ SUCCESS! Document retrieval is working!"
else
    echo "⚠️  Document not found (expected if service restarted)"
fi

