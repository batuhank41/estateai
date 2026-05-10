import requests

url = "http://127.0.0.1:8000/valuation"
data = {
    "address_text": "Kadikoy, Istanbul",
    "rooms": "2+1",
    "area_m2": 100.0,
    "prop_type": "daire",
    "company_id": "test_company"
}

response = requests.post(url, json=data)
print("Status Code:", response.status_code)
print("Response JSON:", response.json())
