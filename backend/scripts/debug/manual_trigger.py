import asyncio
from app.workers.ai_tasks import cumulative_extraction


async def trigger():
    exam_id = "072581a0-8e01-47c6-b15d-7431bf29409e"
    print(f"Manually triggering cumulative_extraction for {exam_id}")
    task = cumulative_extraction.delay(exam_id)
    print(f"Task ID: {task.id}")


if __name__ == "__main__":
    asyncio.run(trigger())
