#!/bin/bash

API_URL="http://localhost:8000"
TEST_FILE="test_documents/test1.png"

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

