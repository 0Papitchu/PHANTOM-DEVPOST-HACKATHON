import requests
import time

print("Stopping session...")
requests.post("http://localhost:8000/api/session/stop")

print("Starting session on Craigslist...")
res = requests.post("http://localhost:8000/api/session/start", json={
    "url": "https://newyork.craigslist.org",
    "headless": True,
    "accessibility_mode": False
})

print(res.status_code)
print(res.text)
