#!/bin/bash

API_URL="http://localhost:8000"
TEST_FILE="test_documents/test1.png"

echo "=========================================="
echo "Full Document Flow Test"
echo "=========================================="
echo ""

# Login
echo "1. Login..."
TOKEN_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin@health-assistant.local&password=admin123")

ACCESS_TOKEN=$(echo $TOKEN_RESPONSE | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)

if [ -z "$ACCESS_TOKEN" ]; then
    echo "   ❌ Login failed"
    exit 1
fi
echo "   ✅ Success"
echo ""

# Upload
echo "2. Upload document..."
UPLOAD_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/documents" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "file=@$TEST_FILE")

DOC_ID=$(echo $UPLOAD_RESPONSE | grep -o '"id":"[^"]*' | cut -d'"' -f4)

if [ -z "$DOC_ID" ]; then
    echo "   ❌ Upload failed"
    echo "   Response: $UPLOAD_RESPONSE"
    exit 1
fi
echo "   ✅ Uploaded! ID: $DOC_ID"
echo ""

# Get document
echo "3. Get document info..."
sleep 1
DOC_RESPONSE=$(curl -s -X GET "${API_URL}/api/v1/documents/$DOC_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

if echo "$DOC_RESPONSE" | grep -q "$DOC_ID"; then
    echo "   ✅ Document retrieved successfully"
    echo "   Status: $(echo $DOC_RESPONSE | grep -o '"status":"[^"]*' | cut -d'"' -f4)"
else
    echo "   ❌ Document not found"
    echo "   Response: $DOC_RESPONSE"
    exit 1
fi
echo ""

# Get extraction status (before trigger)
echo "4. Get extraction status (initial)..."
STATUS1=$(curl -s -X GET "${API_URL}/api/v1/documents/$DOC_ID/extract/status" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

echo "   Response: $STATUS1"
if echo "$STATUS1" | grep -q '"status"'; then
    echo "   ✅ Status endpoint working"
else
    echo "   ⚠️  Status endpoint issue"
fi
echo ""

# Trigger extraction
echo "5. Trigger extraction..."
EXTRACT_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/documents/$DOC_ID/extract" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

echo "   Response: $EXTRACT_RESPONSE"
if echo "$EXTRACT_RESPONSE" | grep -q "job_id"; then
    echo "   ✅ Extraction triggered"
else
    echo "   ⚠️  Extraction may have failed"
fi
echo ""

# Get extraction status (after trigger)
echo "6. Get extraction status (after trigger)..."
sleep 1
STATUS2=$(curl -s -X GET "${API_URL}/api/v1/documents/$DOC_ID/extract/status" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

echo "   Response: $STATUS2"
if echo "$STATUS2" | grep -q "processing"; then
    echo "   ✅ Status changed to processing!"
else
    echo "   ℹ️  Status: $(echo $STATUS2 | grep -o '"status":"[^"]*' | cut -d'"' -f4)"
fi
echo ""

echo "=========================================="
echo "✅ Test Complete!"
echo "=========================================="

