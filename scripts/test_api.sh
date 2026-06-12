#!/bin/bash

# Test configuration
API_URL="http://localhost:8000"
TEST_FILE="test_documents/test1.png"

echo "=========================================="
echo "Health Assistant API Testing"
echo "=========================================="
echo ""

# First, login to get a token
echo "1. Logging in..."
TOKEN_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin@health-assistant.local&password=admin123")

echo "Response:"; echo "$TOKEN_RESPONSE" | jq .

# Extract access token
ACCESS_TOKEN=$(echo $TOKEN_RESPONSE | jq -r '.access_token')

if [ "$ACCESS_TOKEN" == "null" ] || [ -z "$ACCESS_TOKEN" ]; then
    echo "❌ Failed to login. Please check credentials."
    exit 1
fi

echo "✅ Login successful!"
echo ""

# Test 1: Upload Document
echo "2. Testing Document Upload..."
echo "File: $TEST_FILE"
UPLOAD_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/documents" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "file=@$TEST_FILE" \
  -F "patient_id=test-patient-123")

echo "Response:"
echo "$UPLOAD_RESPONSE" | jq .

# Extract document ID
DOC_ID=$(echo $UPLOAD_RESPONSE | jq -r '.id')
echo ""
if [ "$DOC_ID" != "null" ] && [ -n "$DOC_ID" ]; then
    echo "✅ Document uploaded successfully! ID: $DOC_ID"
else
    echo "⚠️  Document uploaded but ID is null (database not connected)"
fi
echo ""

# Test 2: Import CSV (create a test CSV first)
echo "3. Testing CSV Import..."
cat > /tmp/test_lab_results.csv << 'CSVEOF'
date,biomarker,value,unit
2024-01-15,Glucose,95,mg/dL
2024-01-15,Hemoglobin A1c,5.4,%
2024-01-15,Total Cholesterol,180,mg/dL
2024-01-15,HDL Cholesterol,55,mg/dL
2024-01-15,LDL Cholesterol,100,mg/dL
CSVEOF

CSV_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/import/csv" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "file=@/tmp/test_lab_results.csv" \
  -F "patient_id=test-patient-123" \
  -F "delimiter=," \
  -F "has_header=true")

echo "Response:"
echo "$CSV_RESPONSE" | jq .

CSV_STATUS=$(echo $CSV_RESPONSE | jq -r '.status')
if [ "$CSV_STATUS" == "completed" ] || [ "$CSV_STATUS" == "partial" ]; then
    echo "✅ CSV Import successful!"
    echo "   Processed: $(echo $CSV_RESPONSE | jq -r '.processed_records') records"
    echo "   Failed: $(echo $CSV_RESPONSE | jq -r '.failed_records') records"
else
    echo "⚠️  CSV Import status: $CSV_STATUS"
fi
echo ""

# Test 3: Import with OCR
echo "4. Testing OCR Import..."
OCR_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/import/ocr" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "file=@$TEST_FILE" \
  -F "patient_id=test-patient-123" \
  -F "extract_tables=true")

echo "Response:"
echo "$OCR_RESPONSE" | jq .

OCR_STATUS=$(echo $OCR_RESPONSE | jq -r '.status')
if [ "$OCR_STATUS" == "completed" ]; then
    echo "✅ OCR Import successful!"
    if [ -n "$(echo $OCR_RESPONSE | jq -r '.summary')" ]; then
        echo "   Summary: $(echo $OCR_RESPONSE | jq -r '.summary')"
    fi
else
    echo "⚠️  OCR Import status: $OCR_STATUS"
    ERRORS=$(echo $OCR_RESPONSE | jq -r '.errors[]' 2>/dev/null)
    if [ -n "$ERRORS" ]; then
        echo "   Errors:"
        echo "$ERRORS"
    fi
fi
echo ""

# Test 5: Check API Health
echo "5. Checking API Health..."
HEALTH_RESPONSE=$(curl -s "${API_URL}/health")
echo "Response:"
echo "$HEALTH_RESPONSE" | jq .
echo ""

echo "=========================================="
echo "Testing Complete!"
echo "=========================================="

# Cleanup
rm -f /tmp/test_lab_results.csv

