from google import genai

print("Testing Vertex AI (GCP credits)...")
try:
    client = genai.Client(
        vertexai=True,
        project="phantom-ui-navigator",
        location="us-central1",
    )
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents='Say exactly: PHANTOM READY'
    )
    print(f"✅ SUCCESS via Vertex AI! Response: {response.text.strip()}")
except Exception as e:
    print(f"❌ Vertex AI FAILED: {str(e)[:300]}")
