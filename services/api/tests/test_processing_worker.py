import asyncio
import unittest

from app.config import Settings
from app.services.processing_worker import (
    InProcessWorker, JobPriority, PermanentJobError, ProcessingJob,
    RetryableJobError, WorkerBackpressureError, WorkerJobStatus,
    WorkerSupervisor,
)


def job(identifier, *, priority=20, session="s", retries=0, timeout=1000, payload=None):
    return ProcessingJob(
        job_id=identifier, job_type="test", session_id=session,
        segment_id=identifier, revision=1, priority=priority,
        max_retries=retries, timeout_ms=timeout,
        payload=identifier if payload is None else payload,
    )


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.workers = []

    async def asyncTearDown(self):
        for worker in self.workers:
            await worker.shutdown(drain=False)

    def worker(self, handler, *, capacity=8, concurrency=1, **kwargs):
        worker = InProcessWorker("worker-" + str(len(self.workers)), handler, capacity=capacity, concurrency=concurrency, **kwargs)
        self.workers.append(worker)
        return worker

    async def test_priority_ordering(self):
        order = []
        async def handler(value): order.append(value); return value
        worker = self.worker(handler)
        worker._accepting = True
        await worker.submit(job("post", priority=JobPriority.POST_PROCESSING))
        await worker.submit(job("live", priority=JobPriority.LIVE))
        worker._accepting = False
        await worker.start()
        await worker._queue.join()
        self.assertEqual(order, ["live", "post"])

    async def test_bounded_queue_and_backpressure(self):
        worker = self.worker(lambda value: value, capacity=1)
        worker._accepting = True
        await worker.submit(job("a"))
        with self.assertRaises(WorkerBackpressureError):
            await worker.submit(job("b"))
        self.assertEqual(worker.health()["rejectedQueueFull"], 1)

    async def test_concurrency_bounded(self):
        active = maximum = 0
        async def handler(value):
            nonlocal active, maximum
            active += 1; maximum = max(maximum, active)
            await asyncio.sleep(0.01); active -= 1; return value
        worker = self.worker(handler, concurrency=2)
        await worker.start()
        await asyncio.gather(*(worker.submit(job(str(i))) for i in range(6)))
        await worker._queue.join()
        self.assertLessEqual(maximum, 2)

    async def test_duplicate_not_executed(self):
        calls = 0
        async def handler(value):
            nonlocal calls; calls += 1; return value
        worker = self.worker(handler)
        await worker.start()
        await worker.submit(job("same")); await worker.submit(job("same"))
        await worker._queue.join()
        self.assertEqual(calls, 1)

    async def test_retryable_and_permanent_errors(self):
        calls = 0
        async def retryable(value):
            nonlocal calls; calls += 1
            if calls == 1: raise RetryableJobError("again")
            return value
        worker = self.worker(retryable); await worker.start()
        self.assertEqual(await worker.submit_and_wait(job("r", retries=1)), "r")
        self.assertEqual(worker.health()["retried"], 1)

        permanent = self.worker(lambda _: (_ for _ in ()).throw(PermanentJobError("no")))
        await permanent.start()
        with self.assertRaises(PermanentJobError):
            await permanent.submit_and_wait(job("p", retries=2))
        self.assertEqual(permanent.health()["retried"], 0)

    async def test_timeout(self):
        async def slow(_): await asyncio.sleep(0.1)
        worker = self.worker(slow); await worker.start()
        with self.assertRaises(asyncio.TimeoutError):
            await worker.submit_and_wait(job("timeout", timeout=5))
        self.assertEqual(worker.health()["failed"], 1)

    async def test_session_cancellation(self):
        async def slow(value): await asyncio.sleep(0.1); return value
        worker = self.worker(slow, concurrency=1); await worker.start()
        await worker.submit(job("active", session="cancel"))
        await worker.submit(job("pending", session="cancel"))
        await asyncio.sleep(0.005)
        self.assertGreaterEqual(await worker.cancel_session("cancel"), 1)
        await worker._queue.join()
        self.assertEqual(worker.health()["cancelled"], 2)

    async def test_graceful_shutdown_stops_accepting_and_drains(self):
        worker = self.worker(lambda value: value); await worker.start()
        await worker.submit(job("a")); await worker.shutdown(drain=True)
        self.assertFalse(worker.health()["running"])
        with self.assertRaises(RuntimeError): await worker.submit(job("b"))

    async def test_health_model_state(self):
        worker = self.worker(lambda value: value, model_loaded=lambda: True, model_load_time_ms=lambda: 12.5)
        await worker.start(); health = worker.health()
        self.assertTrue(health["ready"]); self.assertTrue(health["modelLoaded"])
        self.assertEqual(health["modelLoadTimeMs"], 12.5)
        await worker.shutdown()
        await worker.start()
        self.assertEqual(worker.health()["workerRestartCount"], 1)

    async def test_worker_failure_isolation_and_live_not_blocked_by_final(self):
        supervisor = WorkerSupervisor()
        final = self.worker(lambda _: (_ for _ in ()).throw(PermanentJobError("final failed")))
        live = self.worker(lambda value: value)
        final.name = "final"; live.name = "live"
        supervisor.register(final); supervisor.register(live); await supervisor.start()
        with self.assertRaises(PermanentJobError): await final.submit_and_wait(job("final-job"))
        self.assertEqual(await live.submit_and_wait(job("live-job", priority=JobPriority.LIVE)), "live-job")
        self.assertTrue(live.health()["running"])

    def test_shared_contract_fields_and_feature_defaults(self):
        fields = job("contract").as_dict()
        for name in ("jobId", "jobType", "sessionId", "segmentId", "revision", "status", "priority", "attempt", "maxRetries", "timeoutMs", "createdAt", "startedAt", "completedAt", "error"):
            self.assertIn(name, fields)
        settings = Settings()
        self.assertFalse(settings.live_pcm_streaming_enabled)
        from app.models.live import CreateLiveSessionRequest
        self.assertEqual(CreateLiveSessionRequest().model, "base")


if __name__ == "__main__": unittest.main()
