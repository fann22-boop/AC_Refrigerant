import requests
import time
import sys

token = "apify_api_cWCpkAaIGPcPBiiljNYSW2qrYZGclP0F5aMm"
run_id = "2EMHwRE6Oe9GRyTOU"
dataset_id = "rdrQGtDZjR847UgAQ"

print(f"📡 Tracking Apify Run: {run_id}")

while True:
    try:
        res = requests.get(f"https://api.apify.com/v2/actor-runs/{run_id}?token={token}")
        data = res.json()
        status = data['data']['status']
        print(f"⏱ Current Status: {status}")
        
        if status == "SUCCEEDED":
            print("✅ Run completed! Downloading CSV...")
            csv_res = requests.get(f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={token}&format=csv")
            with open("taoyuan_auto_leads.csv", "wb") as f:
                f.write(csv_res.content)
            print("💾 Saved to taoyuan_auto_leads.csv")
            break
        elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
            print(f"❌ Run failed with status: {status}")
            sys.exit(1)
        
        time.sleep(10)
    except Exception as e:
        print(f"⚠️ Error: {e}")
        time.sleep(5)
