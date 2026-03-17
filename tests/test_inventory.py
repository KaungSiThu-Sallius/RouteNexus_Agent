from tools import check_inventory_exposure
import json

def run_test():
    print("--- STARTING INVENTORY TOOL TEST ---")
    
    test_region = "Strait of Malacca"
    test_message = "Analyze the Strait of Malacca"
    print(f"\nTesting Region: {test_region}...")
    result_json = check_inventory_exposure(test_message, test_region)

    result = json.loads(result_json)
    
    if result.get("status") == "SUCCESS":
        print(f"✅ Success! Found {result['shipments_exposed']} shipments.")
        print(f"   Total Financial Risk: {result['financial_exposure_usd']}")
        print(f"   Critical Vessel Count: {result['critical_vessels']}")
        print(f"   Vessels: {', '.join(result['vessel_names'])}")
    else:
        print(f"❌ Error: {result.get('message')}")

    print(f"\nTesting Empty Region: Arctic Ocean...")
    empty_result = check_inventory_exposure("Is there any cargo in the Arctic Ocean?", "Arctic Ocean")
    # This path returns a plain string, not JSON
    try:
        parsed = json.loads(empty_result)
        print(f"Output: {parsed}")
    except (json.JSONDecodeError, TypeError):
        print(f"Output: {empty_result}")

if __name__ == "__main__":
    run_test()