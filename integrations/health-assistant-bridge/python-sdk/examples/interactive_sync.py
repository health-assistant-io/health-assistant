import datetime
from health_assistant_bridge import (
    HealthAssistantBridgeClient, 
    SyncPayload, 
    ClientRecord, 
    MetricMappingRequest
)

def main():
    print("=== Health Assistant Bridge: Interactive Sync Example ===")
    base_url = input("Enter Health Assistant Base URL (e.g., http://localhost:8000): ").strip()
    integration_id = input("Enter Integration ID (UUID): ").strip()

    if not base_url or not integration_id:
        print("Base URL and Integration ID are required.")
        return

    client = HealthAssistantBridgeClient(base_url=base_url, integration_id=integration_id)

    # 1. Simulate data scraped from a third-party portal
    print("\n--- 1. Simulating Data Scraping ---")
    scraped_metrics = [
        {"raw_name": "Natrium (Na)", "value": 145.0, "unit": "mmol/L"},
        {"raw_name": "HCT", "value": 45.0, "unit": "%"}
    ]
    print(f"Scraped {len(scraped_metrics)} metrics: {[m['raw_name'] for m in scraped_metrics]}")

    # 2. Ask the AI to map these unrecognized metric names
    print("\n--- 2. Requesting AI Mapping ---")
    mapping_requests = [MetricMappingRequest(name=m["raw_name"]) for m in scraped_metrics]
    
    try:
        mapping_response = client.request_mapping(mapping_requests)
    except Exception as e:
        print(f"Failed to request mapping. Ensure your AI Provider is configured: {e}")
        return

    # 3. Interactively ask the user to confirm the AI's suggestions
    approved_mappings = {}

    for mapping in mapping_response.mappings:
        print(f"\nOriginal Metric Name: '{mapping.original_name}'")
        if mapping.action == "map_to_existing":
            print(f"  -> AI Suggestion: Map to EXISTING biomarker (ID: {mapping.existing_biomarker_id})")
        else:
            print(f"  -> AI Suggestion: Create NEW biomarker '{mapping.new_biomarker_name}'")
            print(f"     (Code: {mapping.new_biomarker_code}, System: {mapping.new_biomarker_coding_system})")
            
        choice = input("Do you approve this mapping? (y/n): ").strip().lower()
        if choice == 'y':
            approved_mappings[mapping.original_name] = mapping
        else:
            print(f"Skipping metric: {mapping.original_name}")

    if not approved_mappings:
        print("\nNo mappings approved. Exiting.")
        return

    # 4. Prepare the strictly-typed Sync Payload
    print("\n--- 3. Preparing Sync Payload ---")
    records = []
    for metric in scraped_metrics:
        raw_name = metric["raw_name"]
        if raw_name in approved_mappings:
            mapping = approved_mappings[raw_name]
            
            # Construct the ClientRecord using the confirmed mapping data
            record = ClientRecord(
                type="quantitative",
                name=mapping.new_biomarker_name or raw_name, # Name is required, fallback to raw if mapping to existing
                biomarker_id=mapping.existing_biomarker_id,
                code=mapping.new_biomarker_code,
                coding_system=mapping.new_biomarker_coding_system or "custom",
                value=metric["value"],
                unit=metric["unit"],
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
            )
            records.append(record)

    payload = SyncPayload(
        client_version="1.0.0",
        source_system="interactive_python_script",
        cursor=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        records=records
    )

    # 5. Push the data to the backend
    print("\n--- 4. Syncing Data to Health Assistant ---")
    try:
        response = client.sync_data(payload)
        if response.success:
            print(f"Success! Synced {response.metrics_synced} records.")
        else:
            print(f"Sync reported failure: {response.error}")
    except Exception as e:
        print(f"Failed to sync data: {e}")

if __name__ == "__main__":
    main()
