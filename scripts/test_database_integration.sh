#!/bin/bash

echo "=========================================="
echo "Testing Database Integration"
echo "=========================================="
echo ""

API_URL="http://localhost:8000"
TEST_FILE="test_documents/test1.png"

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

# List documents (should be empty initially)
echo "2. List documents (initial)..."
DOCS_RESPONSE=$(curl -s -X GET "${API_URL}/api/v1/documents" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

echo "   Response: $DOCS_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "   $DOCS_RESPONSE"
echo ""

# Upload document
echo "3. Upload document..."
UPLOAD_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/documents" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "file=@$TEST_FILE")

echo "   Upload Response:"
echo "$UPLOAD_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$UPLOAD_RESPONSE"

DOC_ID=$(echo $UPLOAD_RESPONSE | grep -o '"id":"[^"]*' | cut -d'"' -f4)

if [ -z "$DOC_ID" ]; then
    echo "   ❌ Upload failed"
    exit 1
fi
echo "   ✅ Uploaded! ID: $DOC_ID"
echo ""

# Wait a moment for database to commit
sleep 1

# List documents again
echo "4. List documents (after upload)..."
DOCS_RESPONSE2=$(curl -s -X GET "${API_URL}/api/v1/documents" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

DOC_COUNT=$(echo $DOCS_RESPONSE2 | python3 -c "import sys, json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
echo "   Document count: $DOC_COUNT"
echo "   Response:"
echo "$DOCS_RESPONSE2" | python3 -m json.tool 2>/dev/null | head -30
echo ""

if [ "$DOC_COUNT" -gt 0 ]; then
    echo "   ✅ Documents persisted in database!"
else
    echo "   ⚠️  No documents found in database"
fi
echo ""

# Get specific document
echo "5. Get specific document..."
DOC_RESPONSE=$(curl -s -X GET "${API_URL}/api/v1/documents/$DOC_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

echo "   Response:"
echo "$DOC_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$DOC_RESPONSE"

if echo "$DOC_RESPONSE" | grep -q "$DOC_ID"; then
    echo "   ✅ Document retrieval working!"
else
    echo "   ❌ Document not found"
fi
echo ""

echo "=========================================="
echo "✅ Database Integration Test Complete!"
echo "=========================================="
echo ""
echo "Next: Refresh your browser and documents should persist!"

