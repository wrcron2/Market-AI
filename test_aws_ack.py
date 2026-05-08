"""
Quick AWS Bedrock connectivity ACK test.
Run from the project root: python test_aws_ack.py
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

required = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"]
missing = [k for k in required if not os.getenv(k)]
if missing:
    print(f"[FAIL] Missing env vars: {', '.join(missing)}")
    sys.exit(1)

print(f"[OK] AWS_REGION       = {os.getenv('AWS_REGION')}")
print(f"[OK] AWS_ACCESS_KEY_ID = {os.getenv('AWS_ACCESS_KEY_ID')[:8]}...")
print(f"[OK] BEDROCK_MODEL_ID  = {os.getenv('BEDROCK_MODEL_ID', 'anthropic.claude-3-5-sonnet-20241022-v2:0')}")
print()
print("Pinging AWS Bedrock...")

sys.path.insert(0, "ai-brain")
from agents.router import LLMRouter, Complexity

router = LLMRouter()
response = router.complete(
    system="You are a connectivity test assistant.",
    user="Reply with exactly: ACK",
    complexity=Complexity.HIGH,
    max_tokens=10,
)

print(f"[RESPONSE] {response}")
if "ACK" in response.upper():
    print("\n✅ AWS Bedrock connection successful!")
else:
    print("\n⚠️  Connected but unexpected response. Check model/permissions.")
