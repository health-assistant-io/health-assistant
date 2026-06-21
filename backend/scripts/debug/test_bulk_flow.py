import httpx
import asyncio
import uuid
import time

API_URL = "http://localhost:8000/api/v1"
AUTH = ("admin@health-assistant.local", "admin123")


async def test_bulk():
    async with httpx.AsyncClient() as client:
        # 1. Login
        res = await client.post(
            f"{API_URL}/auth/login", data={"username": AUTH[0], "password": AUTH[1]}
        )
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("Logged in")

        # 2. Get Patient
        res = await client.get(f"{API_URL}/patients", headers=headers)
        patients_data = res.json()
        patients = patients_data.get("items", [])
        if not patients:
            print("No patients found. Please create one.")
            return
        patient_id = patients[0]["id"]
        print(f"Using patient {patient_id}")

        # 3. Create Exam 1
        res = await client.post(
            f"{API_URL}/examinations",
            headers=headers,
            json={
                "patient_id": patient_id,
                "auto_extract_metadata": True,
                "category": "Group 1",
            },
        )
        exam1_id = res.json()["id"]
        print(f"Created Exam 1: {exam1_id}")

        # 4. Upload Doc 1
        with open("check_status.py", "rb") as f:
            res = await client.post(
                f"{API_URL}/documents",
                headers=headers,
                data={
                    "patient_id": patient_id,
                    "examination_id": exam1_id,
                    "include_in_extraction": "true",
                },
                files={"file": ("doc1.txt", f)},
            )
        print(f"Uploaded Doc 1 to Exam 1: {res.json()['id']}")

        # 5. Create Exam 2
        res = await client.post(
            f"{API_URL}/examinations",
            headers=headers,
            json={
                "patient_id": patient_id,
                "auto_extract_metadata": True,
                "category": "Group 2",
            },
        )
        exam2_id = res.json()["id"]
        print(f"Created Exam 2: {exam2_id}")

        # 6. Upload Doc 2
        with open("check_logs.py", "rb") as f:
            res = await client.post(
                f"{API_URL}/documents",
                headers=headers,
                data={
                    "patient_id": patient_id,
                    "examination_id": exam2_id,
                    "include_in_extraction": "true",
                },
                files={"file": ("doc2.txt", f)},
            )
        print(f"Uploaded Doc 2 to Exam 2: {res.json()['id']}")

        print("\nWaiting for processing...")
        for _ in range(10):
            await asyncio.sleep(2)
            res1 = await client.get(
                f"{API_URL}/examinations/{exam1_id}/status", headers=headers
            )
            res2 = await client.get(
                f"{API_URL}/examinations/{exam2_id}/status", headers=headers
            )
            s1 = res1.json().get("extraction_status")
            s2 = res2.json().get("extraction_status")
            print(f"Status 1: {s1}, Status 2: {s2}")
            if s1 == "completed" and s2 == "completed":
                break


if __name__ == "__main__":
    asyncio.run(test_bulk())
