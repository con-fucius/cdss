import asyncio
import json
import statistics
import time

import httpx

# 50 representative queries (sample)
QUERIES = [
    "What is the first-line ART regimen for adults?",
    "When should I start treatment for cryptococcal meningitis?",
    "What is the dosage of TLD for an adolescent weighing 35kg?",
    "How to manage diabetes in a pregnant woman?",
    "What are the diagnostic criteria for hypertension?",
    "What is the recommended TB treatment for a child?",
    "How frequently should I monitor viral load?",
    "What are the side effects of Dolutegravir?",
    "What are the reference values for HbA1c?",
    "How to treat a patient with both HIV and TB?",
    "What is the first-line treatment for uncomplicated malaria in adults?",
    "How is severe malaria managed in children?",
    "What is the recommended malaria treatment in pregnancy?",
    "What is the dose of artemether-lumefantrine for a 20kg child?",
    "When should IV artesunate be used for malaria?",
]

# Expand to ~50 by repeating with variation
QUERIES = (QUERIES * 4)[:50]

API_URL = "http://localhost:8000/chat/stream"


async def run_benchmark():
    latencies = {
        "ttft": [],  # Time to first token
        "total": [],  # Total completion time
    }

    print(f"Starting benchmark with {len(QUERIES)} queries...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, q in enumerate(QUERIES):
            print(f"Query {i + 1}/{len(QUERIES)}: {q}")
            start_time = time.time()
            first_token_time = None

            try:
                async with client.stream(
                    "POST",
                    API_URL,
                    json={
                        "session_id": f"benchmark_{i}",
                        "message": q,
                        "context": {
                            "patient_type": "Adult",
                            "condition": "Select...",
                            "comorbidity": "Select...",
                            "filters": [],
                        },
                    },
                ) as response:
                    async for chunk in response.aiter_lines():
                        if not chunk.strip():
                            continue
                        if chunk.startswith("data: "):
                            data = json.loads(chunk[6:])
                            if data.get("type") == "chunk" and first_token_time is None:
                                first_token_time = time.time() - start_time
                                latencies["ttft"].append(first_token_time)
            except Exception as e:
                print(f"Error on query {i}: {e}")
                continue

            total_time = time.time() - start_time
            latencies["total"].append(total_time)
            print(f"  TTFT: {first_token_time:.2f}s, Total: {total_time:.2f}s")

            await asyncio.sleep(0.5)  # short pause

    print("\n--- Benchmark Results ---")
    if latencies["ttft"]:
        print(f"Average TTFT: {statistics.mean(latencies['ttft']):.2f}s")
        print(f"P95 TTFT: {statistics.quantiles(latencies['ttft'], n=20)[18]:.2f}s")
    if latencies["total"]:
        print(f"Average Total: {statistics.mean(latencies['total']):.2f}s")
        print(f"P95 Total: {statistics.quantiles(latencies['total'], n=20)[18]:.2f}s")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
