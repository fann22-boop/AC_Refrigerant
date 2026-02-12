import requests
import time
import os
import sys

# Load token from .env or use the one found
token = "apify_api_cWCpkAaIGPcPBiiljNYSW2qrYZGclP0F5aMm"
actor_id = "compass~crawler-google-places"

input_data = {
    "searchStringsArray": [
        "汽車修理廠",
        "汽車材料店"
    ],
    "locationQuery": "桃園市桃園區",
    "maxCrawledPlacesPerSearch": 200,
    "language": "zh-TW"
}

print(f"Starting Apify Actor {actor_id}...")
run_url = f"https://api.apify.com/v2/acts/{actor_id}/runs?token={token}"
response = requests.post(run_url, json=input_data)

if response.status_code != 201:
    print(f"Error starting run: {response.text}")
    sys.exit(1)

run_info = response.json()
run_id = run_info['data']['id']
dataset_id = run_info['data']['defaultDatasetId']

print(f"Run started. Run ID: {run_id}")
print(f"Dataset ID: {dataset_id}")

# Wait for completion
status_url = f"https://api.apify.com/v2/acts/{actor_id}/runs/{run_id}?token={token}"
while True:
    status_response = requests.get(status_url)
    status_data = status_response.json()['data']
    status = status_data['status']
    print(f"Current status: {status}")
    if status in ['SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT']:
        break
    time.sleep(10)

if status in ['SUCCEEDED', 'ABORTED']:
    print("Run succeeded. Exporting CSV...")
    csv_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?format=csv&token={token}"
    csv_response = requests.get(csv_url)
    
    filename = "taoyuan_car_repair_shops.csv"
    with open(filename, "wb") as f:
        f.write(csv_response.content)
    print(f"Done! Results saved to {filename}")
else:
    print(f"Run failed with status: {status}")
