import requests
from requests_ntlm import HttpNtlmAuth

import truststore
truststore.inject_into_ssl()

BASE_URL = "https://gev.finma.qa/FINMA/api/data/v9.1"
auth = HttpNtlmAuth("DOMAIN\\username", "password")  # use proper auth for prod

headers = {
    "Accept": "application/json",
    "Content-Type": "application/json; charset=utf-8",
    "OData-MaxVersion": "4.0",
    "OData-Version": "4.0",
}

payload = {
    "finma_datumvon": "2026-11-11",
    "finma_geschaeftsstatus": 178090000,
    "finma_name": "Test SIRIUS API HEDA",
    "finma_betreff": "Test SIRIUS API HEDA",
    "finma_finmaobjekt@odata.bind": "/accounts(b47ab8b6-3262-4116-9b90-0003889de149)",
    "finma_geschaeftsprozess@odata.bind": "/finma_businessprocesses(c1dc66a8-1dbf-e311-8283-005056bf0754)",
    "finma_teilprozess@odata.bind": "/finma_subprocesses(733f3b35-873c-e711-b3bc-005056bf56d2)",
}

response = requests.post(f"{BASE_URL}/finma_geschaeftthemas", json=payload, headers=headers, auth=auth)

if response.status_code == 204:
    print("Created:", response.headers["OData-EntityId"])
else:
    print(response.status_code, response.text)